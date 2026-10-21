"""Verify install archive hashes and load its extracted AstrBot namespace offline."""

import asyncio
import hashlib
import importlib
import json
import re
import runpy
import struct
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    name = re.search(r"^name:\s*(\w+)\s*$", metadata, re.MULTILINE)[1]
    version = re.search(r"^version:\s*(\d+\.\d+\.\d+)\s*$", metadata, re.MULTILINE)[1]
    archive_path = ROOT / "dist" / f"{name}-v{version}.zip"
    manifest = json.loads((ROOT / "dist" / f"manifest-v{version}.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="easonclaw-package-") as temporary:
        plugins = Path(temporary) / "data" / "plugins"
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            assert len(archive.namelist()) == len(set(archive.namelist()))
            assert set(archive.namelist()) == set(manifest)
            for entry, digest in manifest.items():
                relative = PurePosixPath(entry)
                assert relative.parts[0] == name
                assert not relative.is_absolute() and ".." not in relative.parts
                assert "\\" not in entry and ":" not in entry
                raw = archive.read(entry)
                assert hashlib.sha256(raw).hexdigest() == digest, entry
                assert raw == (ROOT / Path(*relative.parts[1:])).read_bytes(), entry
            archive.extractall(plugins)
        extracted = plugins / name
        logo = (extracted / "logo.png").read_bytes()
        assert logo[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", logo[16:24]) == (512, 512)
        assert (extracted / "logo.svg").is_file() and (extracted / "NOTICE").is_file()

        # Only framework boundaries are doubled; import and plugin logic use the ZIP.
        # This script runs in its own process, so these doubles cannot affect AstrBot.
        runpy.run_path(str(ROOT / "tests" / "conftest.py"))
        sys.path.insert(0, temporary)
        module = importlib.import_module(f"data.plugins.{name}.main")
        assert Path(module.__file__).resolve() == (extracted / "main.py").resolve()

        class Config(dict):
            def save_config(self):
                pass

        config = Config(json.loads((extracted / "config.example.json").read_text(encoding="utf-8")))
        config["enable_auto_monitor"] = False

        async def smoke():
            plugin = module.MyPlugin(SimpleNamespace(), config)
            await plugin.initialize()
            event = SimpleNamespace(
                get_group_id=lambda: "123456789",
                unified_msg_origin="qq-main:GroupMessage:123456789",
            )
            assert [server.key for server in plugin._visible_servers(event)] == ["survival"]
            event.unified_msg_origin = "other-bot:GroupMessage:123456789"
            try:
                plugin._visible_servers(event)
            except ValueError:
                pass
            else:
                raise AssertionError("Other platform instance gained access")
            assert "EasonClaw / BLOCK WATCH" in plugin.renderer.template
            await plugin.terminate()
            assert plugin.closed and plugin.renderer.closed and not plugin.renderer.tasks

        asyncio.run(smoke())
    result = {
        "package": name,
        "version": version,
        "files": len(manifest),
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "source_hashes_match": True,
        "extracted_namespace_import": True,
        "group_isolation_smoke": True,
        "logo": "512x512 PNG + SVG",
    }
    (ROOT / "dist" / f"verification-v{version}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
