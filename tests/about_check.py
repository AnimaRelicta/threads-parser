"""Проверка «Об этом профиле» без сети: разбор реального HTML окна + сценарий кликов на макете.

Запуск: uv run python tests/about_check.py
"""

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from playwright.async_api import async_playwright  # noqa: E402

from core.about import parse_about  # noqa: E402
from scrape.about import fetch_about, read_about_rows  # noqa: E402

DIALOG_HTML = (HERE / "fixtures" / "about_dialog.html").read_text(encoding="utf-8")

# Макет профиля: значок «···» в шапке и в посте (как на настоящей странице), меню, окно.
MOCK = (
    """<html><body style="margin:0">
<div role="button" id="sb" style="position:absolute;left:20px;top:10px;width:36px;height:36px">
  <svg viewBox="0 0 24 24" role="img"><title>Ещё</title><path d="M1 1"/></svg></div>
<div role="button" id="hb" aria-haspopup="dialog" style="position:absolute;left:900px;top:346px;width:36px;height:36px">
  <svg viewBox="0 0 24 24" role="img"><title>Ещё</title><path d="M1 1"/></svg></div>
<div id="post" style="position:absolute;left:900px;top:700px;width:36px;height:36px;font-weight:700">
  <div role="button"><svg viewBox="0 0 24 24" role="img"><title>Ещё</title><path d="M1 1"/></svg></div></div>
<div id="menu" style="display:none"><div role="menuitem"><span>Заблокировать</span></div>
  <div role="menuitem"><span><span>Об этом профиле</span></span></div></div>
<div id="sbm" style="display:none;position:absolute;left:60px;top:10px"><div role="menuitem"><span>Настройки</span></div></div>
<div id="dlg" role="dialog" style="display:none">"""
    + DIALOG_HTML
    + """</div>
<script>
 document.querySelector('#sb').onclick = () => { sbm.style.display='block'; };
 document.querySelector('#hb').onclick = () => menu.style.display = 'block';
 document.querySelector('#menu [role=menuitem]:nth-child(2)').onclick = () => {
   menu.style.display = 'none'; setTimeout(() => dlg.style.display = 'block', 300); };
 document.addEventListener('keydown', e => { if (e.key === 'Escape') {
   dlg.style.display = 'none'; menu.style.display = 'none'; sbm.style.display='none'; } });
</script></body></html>"""
)


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # 1. Разбор настоящего HTML окна (из ТЗ)
        await page.set_content(f"<body>{DIALOG_HTML}</body>")
        rows = await read_about_rows(page)
        print("rows:", rows)
        a = parse_about(rows)
        assert a.name == "Минская квартира" and a.username == "by_kvartiraminsk", a
        assert a.joined == "Октябрь 2026 г.", a.joined
        assert a.location == "Беларусь", a.location
        assert len(a.raw) == 3, a.raw

        # 2. Сценарий кликов: значок шапки -> меню -> окно -> закрытие
        await page.set_content(MOCK)
        about = await fetch_about(page, "about:blank")
        assert about and about.location == "Беларусь", about
        assert not await read_about_rows(page), "окно должно быть закрыто"
        print("about:", about)

        # 3. Нет пункта в меню -> None, без исключения
        await page.set_content(
            MOCK.replace("Об этом профиле</span></span>", "Другое</span></span>")
        )
        assert await fetch_about(page, "about:blank") is None
        await browser.close()
    print("OK")
    return 0


raise SystemExit(asyncio.run(main()))
