"""«Об аккаунте»: открывает окно через «···» на странице профиля и снимает его целиком.

Сценарий: профиль -> «···» в шапке (svg «Параметры»/«Options») -> пункт «Об аккаунте» ->
текст и HTML окна -> закрыть. Кнопка и пункт найдены по образцу сбора «Об этом профиле»
в Threads; ВЕРСТКУ INSTAGRAM ПРОВЕРИТЬ НА ЖИВОЙ СЕССИИ."""

import re

from playwright.async_api import Error as PlaywrightError

from core.logs import log
from ig.about import is_about_text, parse_about_text

MENU_ITEM = re.compile(r"^(Об аккаунте|About this account)$", re.I)
ICON_NAMES = ("Параметры", "Options", "Ещё", "More")
MAX_ICONS = 2  # сколько верхних кнопок «···» пробуем
SIDEBAR_SHARE = 0.15  # левая панель: центр кнопки левее этой доли ширины окна
HTML_LIMIT = 300_000  # символов HTML окна в about_raw

FIND_ICONS_JS = r"""
(names) => {
    const out = [];
    for (const svg of document.querySelectorAll('svg')) {
        const t = svg.querySelector(':scope > title');
        const btn = svg.closest('[role="button"], button') || svg;
        const name = ((t && t.textContent) || svg.getAttribute('aria-label')
                      || btn.getAttribute('aria-label') || '').trim();
        if (!names.includes(name)) continue;
        const r = btn.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, vw: window.innerWidth,
                  header: !!btn.closest('header')});
    }
    return out;
}
"""

DIALOGS_JS = r"""
() => [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
    .map(d => ({text: (d.innerText || '').trim(), html: d.outerHTML}))
"""


async def _icons(page) -> list[dict]:
    """Кнопки «···» вне левой панели: сначала из <header>, затем сверху вниз."""
    found = await page.evaluate(FIND_ICONS_JS, list(ICON_NAMES))
    main = [i for i in found if i["x"] > i["vw"] * SIDEBAR_SHARE]
    return sorted(main, key=lambda i: (not i["header"], i["y"]))


async def _read_window(page, timeout_ms: int) -> dict | None:
    """Ждёт окно «Об аккаунте» и возвращает его {text, html}; None — не появилось."""
    waited = 0
    while waited < timeout_ms:
        for d in await page.evaluate(DIALOGS_JS):
            if is_about_text(d["text"]):
                return d
        await page.wait_for_timeout(250)
        waited += 250
    return None


async def _close(page, url: str) -> None:
    """Закрывает окно (Escape); если не закрылось, перезагружает профиль."""
    for _ in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        if not [d for d in await page.evaluate(DIALOGS_JS) if is_about_text(d["text"])]:
            return
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)


async def fetch_about(page, url: str) -> dict | None:
    """Окно «Об аккаунте» открытого профиля -> {fields, raw}; None, если открыть не удалось.

    fields: joined_*, account_location, verified_*, is_verified; raw: text, html (обрезан до
    HTML_LIMIT, html_truncated), rows. Ошибки браузера не пробрасываются: сбор окна не должен
    ронять обход профиля."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
        icons = await _icons(page)
        if not icons:
            log("    [!] «Об аккаунте»: на странице нет кнопки «···»")
            return None
        item = (
            page.locator('[role="menuitem"], button')
            .filter(has_text=MENU_ITEM)
            .or_(page.get_by_text(MENU_ITEM))
            .first
        )
        for n, ic in enumerate(icons[:MAX_ICONS], 1):
            await page.mouse.click(ic["x"], ic["y"])
            try:
                await item.wait_for(state="visible", timeout=4000)
                await item.click(timeout=3000)
            except PlaywrightError:
                where = f"x={ic['x']:.0f}, y={ic['y']:.0f}"
                log(f"    [*] «···» #{n} ({where}): пункта «Об аккаунте» нет")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
                await page.evaluate("window.scrollTo(0, 0)")
                continue
            win = await _read_window(page, 8000)
            await _close(page, url)
            if win is None:
                log("    [!] «Об аккаунте»: окно открылось, но данных в нём не нашли")
                return None
            fields = parse_about_text(win["text"])
            rows = fields.pop("rows")
            html = win["html"]
            raw = {
                "text": win["text"],
                "html": html[:HTML_LIMIT],
                "html_truncated": len(html) > HTML_LIMIT,
                "rows": rows,
            }
            return {"fields": fields, "raw": raw}
        tried = min(len(icons), MAX_ICONS)
        log(f"    [!] «Об аккаунте»: пункт не нашли ни в одной из {tried} кнопок")
    except PlaywrightError as e:
        log(f"    [!] «Об аккаунте»: ошибка браузера: {str(e)[:150]}")
    return None
