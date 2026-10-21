"""Render production templates/options in Edge and verify content-sized PNGs."""

import asyncio
import json
import math
import struct
import sys
from dataclasses import replace
from pathlib import Path

from jinja2 import Environment
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minecraft_multi_monitor.models import DisplayOptions, ServerSnapshot  # noqa: E402
from minecraft_multi_monitor.rendering import CardRenderer  # noqa: E402


async def main():
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    report = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        case = ""
        initial_size = {"width": 1280, "height": 720}

        async def render(template, data, *, options):
            # These custom options are consumed by AstrBot's official T2I service;
            # remaining options are passed to Playwright screenshot unchanged.
            screenshot_options = dict(options)
            viewport = {
                "width": screenshot_options.pop("viewport_width", initial_size["width"]),
                "height": screenshot_options.pop("viewport_height", initial_size["height"]),
            }
            await page.set_viewport_size(viewport)
            document = Environment(autoescape=True).from_string(template).render(**data)
            await page.set_content(document, wait_until="load")
            await page.evaluate("document.fonts.ready")
            dimensions = await page.evaluate("""() => ({
                bodyWidth: document.body.getBoundingClientRect().width,
                bodyHeight: document.body.getBoundingClientRect().height,
                scrollWidth: document.documentElement.scrollWidth,
                viewportWidth: innerWidth,
                footerBottom: document.querySelector('.footer').getBoundingClientRect().bottom,
                cards: document.querySelectorAll('section.card').length,
                injectedScripts: document.querySelectorAll('script').length,
                resourceImages: [...document.images].filter(img => !img.src.startsWith('data:')).length,
                grassBlocks: document.querySelectorAll('svg').length,
                quote: !!document.querySelector('.quote'),
                visibleText: document.body.innerText
            })""")
            assert dimensions["scrollWidth"] <= viewport["width"], dimensions
            assert dimensions["injectedScripts"] == 0
            assert dimensions["resourceImages"] == 0
            assert dimensions["grassBlocks"] >= 1
            assert "EasonClaw / BLOCK WATCH" in dimensions["visibleText"]
            assert "astrbot" not in dimensions["visibleText"].lower()
            filename = f"{case}.png" if data["pages"] == 1 else f"{case}-{data['page']}.png"
            target = output / filename
            raw = await page.screenshot(path=str(target), **screenshot_options)
            width, height = struct.unpack(">II", raw[16:24])
            assert width == 800
            assert abs(height - math.ceil(dimensions["bodyHeight"])) <= 2, (height, dimensions)
            assert 0 <= height - dimensions["footerBottom"] <= 50, (height, dimensions)
            assert height <= 1500, (case, height)
            if "offline" in case:
                assert height < 720, (case, height)
                assert "离线" in dimensions["visibleText"]
            if "long" in case:
                assert "<script>" in dimensions["visibleText"]
            report.append(
                {
                    "file": filename,
                    "width": width,
                    "height": height,
                    "initialViewport": dict(initial_size),
                    "requestedViewport": viewport,
                    "bottomBlank": round(height - dimensions["footerBottom"], 2),
                    **{key: value for key, value in dimensions.items() if key != "visibleText"},
                }
            )
            return str(target)

        snapshot = ServerSnapshot(
            "survival",
            "方块世界 · 生存服",
            "play.example.com",
            25565,
            "java",
            "online",
            "欢迎回家！一起探索属于我们的方块世界。",
            "1.21.4",
            "769",
            12,
            40,
            ["Alex", "Steve", "小明"],
            "Paper",
            "world",
            "2026-10-21 10:00:00 +0800",
            True,
            source="mcstatus.io",
        )
        offline = replace(
            snapshot,
            name="示例生存服",
            status="offline",
            online=0,
            players=[],
            source="MineBBS",
            error="无法连接到服务器，它可能已离线或地址/端口不正确。",
        )
        unknown = replace(
            snapshot, name="活动服 · 周末探险", status="unknown", error="查询源不可用，请稍后重试", source=""
        )
        options = DisplayOptions(show_game_version=True)
        fixtures = [
            ("status-overview", [snapshot, offline, unknown], options, ""),
            (
                "status-offline-quote",
                [offline],
                replace(options, show_server_address=True),
                "日落之后，也别忘了回家。",
            ),
            (
                "status-offline-compact",
                [offline],
                DisplayOptions(show_server_type=False, show_update_time=False),
                "",
            ),
            (
                "status-long-text",
                [
                    replace(
                        snapshot,
                        name="<script>alert(1)</script>" + "超长服务器名称" * 12,
                        motd="<img src=x onerror=alert(1)>" + "超长MOTD " * 50,
                    )
                ],
                DisplayOptions(show_server_address=True, show_game_version=True, show_player_list=True),
                "<b>请把石头留给下一位冒险家。</b>",
            ),
            ("status-four", [replace(snapshot, name=f"冒险世界 {i + 1}") for i in range(4)], options, ""),
            ("status-five", [replace(snapshot, name=f"冒险世界 {i + 1}") for i in range(5)], options, ""),
        ]
        for name, snapshots, display, quote in fixtures:
            case = name
            renderer = CardRenderer(render)
            await renderer.render_pages(snapshots, display, quote)
            await renderer.close()
        for initial_size in ({"width": 600, "height": 1200}, {"width": 1600, "height": 300}):
            case = f"status-offline-viewport-{initial_size['width']}"
            renderer = CardRenderer(render)
            await renderer.render_pages([offline], options)
            await renderer.close()
        await browser.close()
    (output / "preview-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            [{key: item[key] for key in ("file", "width", "height", "bottomBlank")} for item in report],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
