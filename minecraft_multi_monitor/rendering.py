import asyncio
import base64
import hashlib
import html
import json
import struct
import time
from collections import OrderedDict
from pathlib import Path

from .monitoring import server_info_fields

LOCK_TIMEOUT = 10


def safe_icon(value):
    prefix = "data:image/png;base64,"
    if not value.startswith(prefix) or len(value) > 40000:
        return ""
    try:
        raw = base64.b64decode(value[len(prefix) :], validate=True)
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            return ""
        width, height = struct.unpack(">II", raw[16:24])
        return value if 0 < width <= 256 and 0 < height <= 256 else ""
    except (ValueError, struct.error):
        return ""


class CardRenderer:
    def __init__(self, render):
        self.render = render
        self.template = (Path(__file__).parent / "templates" / "status.html").read_text(encoding="utf-8")
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.closed = False
        self.tasks = set()

    async def render_pages(self, snapshots, options, quote=""):
        if self.closed:
            raise RuntimeError("图片渲染器已关闭")
        task = asyncio.create_task(self._render_pages(snapshots, options, quote))
        self.tasks.add(task)
        try:
            return await task
        finally:
            self.tasks.discard(task)

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()

    def invalidate(self, image):
        # A failed upload can mean the cached URL/path is no longer usable.
        for key, (_, result) in list(self.cache.items()):
            if result == image:
                self.cache.pop(key)

    async def _render_pages(self, snapshots, options, quote):
        rows = []
        for snapshot in snapshots:
            rows.append(
                {
                    "name": html.escape(
                        snapshot.name[:100] if options.show_server_name else "Minecraft 服务器"
                    ),
                    "status": snapshot.status
                    if snapshot.status in {"online", "offline", "unknown"}
                    else "unknown",
                    "label": {"online": "在线", "offline": "离线"}.get(snapshot.status, "查询异常"),
                    "fields": [
                        {
                            "label": html.escape(label),
                            "value": html.escape(value),
                            "wide": label in {"地址", "MOTD", "玩家样本（可能不完整）", "说明", "数据时间"},
                        }
                        for _, label, value in server_info_fields(snapshot, options)
                    ],
                    "source": html.escape(snapshot.source),
                    "icon": safe_icon(snapshot.icon),
                    "ratio": min(100, max(0, snapshot.online * 100 // snapshot.max_players))
                    if snapshot.max_players > 0
                    and options.show_online_players
                    and snapshot.status == "online"
                    else None,
                }
            )
        pages = [rows[index : index + 4] for index in range(0, len(rows), 4)]
        results = []
        started = time.monotonic()
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=LOCK_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"stage=lock wait={time.monotonic() - started:.2f}s") from exc
        waited = time.monotonic() - started
        try:
            for index, page in enumerate(pages, 1):
                data = {
                    "servers": page,
                    "page": index,
                    "pages": len(pages),
                    "quote": html.escape(quote[:200]),
                }
                key = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
                cached = self.cache.get(key)
                if cached and time.monotonic() - cached[0] < 30:
                    self.cache.move_to_end(key)
                    results.append(cached[1])
                    continue
                result = await asyncio.wait_for(
                    self.render(
                        self.template,
                        data,
                        options={
                            "type": "png",
                            "full_page": True,
                            "viewport_width": 800,
                            "viewport_height": 1,
                        },
                    ),
                    timeout=20,
                )
                if not isinstance(result, str) or not result:
                    raise ValueError("渲染服务未返回图片路径或 URL")
                self.cache[key] = (time.monotonic(), result)
                while len(self.cache) > 32:
                    self.cache.popitem(last=False)
                results.append(result)
        except Exception as exc:
            raise RuntimeError(
                f"stage=render wait={waited:.2f}s elapsed={time.monotonic() - started:.2f}s "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            self.lock.release()
        return results
