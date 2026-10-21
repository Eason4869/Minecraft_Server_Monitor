"""Export the editable vector logo to AstrBot's required logo.png."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        page = await browser.new_page(viewport={"width": 512, "height": 512}, device_scale_factor=1)
        await page.goto((ROOT / "logo.svg").as_uri())
        await page.locator("svg").screenshot(path=str(ROOT / "logo.png"), omit_background=True)
        await browser.close()
    print("logo.png: 512 x 512")


if __name__ == "__main__":
    asyncio.run(main())
