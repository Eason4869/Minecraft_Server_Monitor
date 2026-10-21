"""Build a clean install ZIP and verify every file after reading it back."""

import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "astrbot_plugin_minecraft_monitor"


def main():
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*(\d+\.\d+\.\d+)\s*$", metadata, re.MULTILINE)
    if match is None:
        raise ValueError("metadata.yaml 缺少有效版本号")
    version = match[1]
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    files = [
        ROOT / name
        for name in (
            "main.py",
            "metadata.yaml",
            "_conf_schema.json",
            "requirements.txt",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "NOTICE",
            "config.example.json",
            "logo.png",
            "logo.svg",
        )
    ]
    files += [
        path
        for path in (ROOT / "minecraft_multi_monitor").rglob("*")
        if path.is_file() and path.suffix in {".py", ".html"}
    ]
    files.append(ROOT / "docs" / "images" / "status-overview.png")
    files.append(ROOT / "docs" / "images" / "status-offline-quote.png")
    target = output / f"{NAME}-v{version}.zip"
    hashes = {}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            entry = f"{NAME}/{path.relative_to(ROOT).as_posix()}"
            data = path.read_bytes()
            archive.writestr(entry, data)
            hashes[entry] = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(hashes)
        for entry, digest in hashes.items():
            assert hashlib.sha256(archive.read(entry)).hexdigest() == digest
    (output / f"manifest-v{version}.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    sums = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(output.glob(f"{NAME}-v*.zip"))
    ]
    (output / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(target), "files": len(files), "sha256": digest}))


if __name__ == "__main__":
    main()
