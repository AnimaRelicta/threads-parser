"""«Об этом профиле»: открывает окно через «···» на странице профиля и читает его строки.

Сценарий: профиль -> значок «···» (svg с title «Ещё») в шапке -> пункт «Об этом профиле»
-> пары «заголовок → значение» -> закрыть окно. Био из JSON страницы (core/biography.py)
этого окна не заменяет: здесь имя, дата регистрации, местоположение.
"""

import re

from playwright.async_api import Error as PlaywrightError

from core.about import About, parse_about
from core.logs import log

# Значок «···» в шапке профиля: div[role=button] с svg-заголовком «Ещё»/«More». Таких кнопок
# на странице несколько (левая панель, шапка, меню каждого поста), поэтому выбираем по
# геометрии: не в левой панели и выше остальных (по записи кликов шапка стоит над постами).
MENU_ITEM = re.compile(r"^(Об этом профиле|About this profile)$", re.I)
MAX_ICONS = 0  # сколько верхних кнопок «···» пробуем, прежде чем сдаться
SIDEBAR_SHARE = 0.15  # левая панель: центр кнопки левее этой доли ширины окна

FIND_ICONS_JS = r"""
() => {
    const out = [];
    for (const svg of document.querySelectorAll('svg')) {
        const t = svg.querySelector(':scope > title');
        const name = t && t.textContent.trim();
        if (name !== 'Ещё' && name !== 'More') continue;
        const btn = svg.closest('[role="button"], button') || svg;
        const r = btn.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, popup: btn.getAttribute('aria-haspopup'),
                  vw: window.innerWidth});
    }
    return out;
}
"""

# Строки окна: заголовок = жирный (>=600) листовой span, значение = остальной текст
# колонки, в которой он лежит. Окно рисует Bloks (div.wbloks_1), на нём и ограничиваемся,
# чтобы не зацепить жирные тексты ленты.
READ_ROWS_JS = r"""
() => {
    const dialogs = document.querySelectorAll('[role="dialog"]');
    const root = dialogs.length ? dialogs[dialogs.length - 1] : document;
    const clean = s => (s || '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
    const rows = [], seen = new Set();
    for (const lab of root.querySelectorAll('.wbloks_1 span')) {
        if (lab.children.length) continue;
        if (parseInt(getComputedStyle(lab).fontWeight, 10) < 600) continue;
        const head = lab.parentElement, col = head && head.parentElement;
        if (!col) continue;
        const label = clean(lab.textContent);
        if (!label || seen.has(label)) continue;
        const parts = [];
        for (const ch of col.children) {
            if (ch === head) continue;
            const t = clean(ch.textContent);
            if (t) parts.push(t);
        }
        const value = parts.join(' ');
        if (!value) continue;
        seen.add(label);
        rows.push([label, value]);
    }
    return rows;
}
"""


async def read_about_rows(page) -> list[list[str]]:
    return await page.evaluate(READ_ROWS_JS)


async def _wait_rows(page, timeout_ms: int) -> list[list[str]]:
    """Ждёт появления строк окна (оно подгружается после клика)."""
    waited = 0
    while waited < timeout_ms:
        rows = await read_about_rows(page)
        if rows:
            return rows
        await page.wait_for_timeout(250)
        waited += 250
    return []


async def _close(page, url: str) -> None:
    """Закрывает окно (Escape); если не закрылось, перезагружает профиль."""
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    if await read_about_rows(page):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    if await read_about_rows(page):
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)


async def _icons(page) -> list[dict]:
    """Кнопки «···» вне левой панели: сначала с popup=dialog, затем сверху вниз."""
    found = await page.evaluate(FIND_ICONS_JS)
    main = [i for i in found if i["x"] > i["vw"] * SIDEBAR_SHARE]
    # шапка профиля по записи кликов: aria-haspopup="dialog"; «menu» — верхняя панель
    return sorted(main, key=lambda i: (i["popup"] != "dialog", i["y"]))


async def fetch_about(page, url: str) -> About | None:
    """Окно «Об этом профиле» открытого профиля; None, если открыть не удалось.

    Страница должна быть уже на профиле. Ошибки браузера не пробрасываются: сбор
    «Об этом профиле» не должен ронять обход профиля."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
        icons = await _icons(page)
        if not icons:
            log("    [!] «Об этом профиле»: на странице нет кнопки «···» (svg «Ещё»)")
            return None
        item = page.locator('[role="menuitem"]').filter(has_text=MENU_ITEM).first
        for n, ic in enumerate(icons[:MAX_ICONS], 1):
            await page.mouse.click(ic["x"], ic["y"])
            try:
                await item.wait_for(state="visible", timeout=4000)
                await item.click(timeout=3000)
            except PlaywrightError:
                log(
                    f"    [*] «···» #{n} (x={ic['x']:.0f}, y={ic['y']:.0f}, "
                    f"popup={ic['popup']}): пункта «Об этом профиле» нет"
                )
                await page.keyboard.press("Escape")  # не то меню: закрываем, пробуем следующий
                await page.wait_for_timeout(400)
                await page.evaluate("window.scrollTo(0, 0)")
                continue
            rows = await _wait_rows(page, 8000)
            await _close(page, url)
            about = parse_about(rows)
            if about:
                return about
            log("    [!] «Об этом профиле»: окно открылось, но строк не нашли")
            return None
        log(
            f"    [!] «Об этом профиле»: пункт не нашли ни в одной из {min(len(icons), MAX_ICONS)} кнопок"
        )
    except PlaywrightError as e:
        log(f"    [!] «Об этом профиле»: ошибка браузера: {str(e)[:150]}")
    return None
