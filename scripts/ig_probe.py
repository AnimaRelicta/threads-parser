"""Диагностика одного профиля Instagram: что отвечают страница и API (запуск вручную).

Положить в scripts/ и выполнить:
    uv run scripts/ig_probe.py garikkharlamov account_001
Окно браузера откроется (не headless), ничего не пишется в БД.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import async_playwright  # noqa: E402

from browser.launch import launch_browser, new_context  # noqa: E402
from config import IG, IG_APP_ID, load_ig_settings  # noqa: E402
from ig.browser.session import load_cookies  # noqa: E402

JS = """async (url) => {
  const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  const r = await fetch(url, {credentials: 'include', headers: {
    'X-IG-App-ID': '%s', 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'}});
  return {status: r.status, body: (await r.text()).slice(0, 1500)};
}""" % IG_APP_ID


async def main(nick: str, account: str) -> None:
    st = load_ig_settings()
    data = load_cookies(st.cookies_path(account))
    if not data:
        raise SystemExit(f"нет кук аккаунта {account}")
    async with async_playwright() as p:
        browser = await launch_browser(p, False, None)
        ctx = await new_context(browser, data)
        page = await ctx.new_page()
        resp = await page.goto(f"{IG}/{nick}/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        print("страница: статус", resp.status if resp else None, "| url", page.url)
        print("title:", await page.title())
        text = await page.evaluate("document.body.innerText.slice(0, 300)")
        print("текст страницы:", text.replace("\n", " | "))
        api = await page.evaluate(JS, f"/api/v1/users/web_profile_info/?username={nick}")
        print("web_profile_info: статус", api["status"])
        print(api["body"])
        await page.pause()  # посмотреть окно; закрыть инспектор для выхода
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "account_001"))
