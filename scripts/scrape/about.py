"""«Об этом профиле»: открывает окно через «···» на странице профиля и читает его строки.

Сценарий: профиль -> значок «···» (svg с title «Ещё») в шапке -> пункт «Об этом профиле»
-> пары «заголовок → значение» -> закрыть окно. Био из JSON страницы (core/biography.py)
этого окна не заменяет: здесь имя, дата регистрации, местоположение.
"""

import re

from playwright.async_api import Error as PlaywrightError

from core.about import KNOWN_LABELS, About, parse_about
from core.logs import log

# Значок «···» в шапке профиля: div[role=button] с svg-заголовком «Ещё»/«More». Таких кнопок
# на странице несколько (левая панель, шапка, меню каждого поста), поэтому выбираем по
# геометрии: не в левой панели и выше остальных (по записи кликов шапка стоит над постами).
MENU_ITEM = re.compile(r"^(Об этом профиле|About this profile)$", re.I)
MAX_ICONS = 1  # сколько верхних кнопок «···» пробуем, прежде чем сдаться
SIDEBAR_SHARE = 0.15  # левая панель: центр кнопки левее этой доли ширины окна

FIND_ICONS_JS = r"""
() => {
    const out = [];
    for (const svg of document.querySelectorAll('svg')) {
        const t = svg.querySelector(':scope > title');
        const btn = svg.closest('[role="button"], button') || svg;
        const name = ((t && t.textContent) || svg.getAttribute('aria-label')
                      || btn.getAttribute('aria-label') || '').trim();
        if (name !== 'Ещё' && name !== 'More') continue;
        const r = btn.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, popup: btn.getAttribute('aria-haspopup'),
                  vw: window.innerWidth});
    }
    return out;
}
"""

# Строки окна читаем без опоры на вёрстку (классы, <li>, wbloks генерируются и меняются).
# Берём листья с текстом в порядке документа и собираем пары «заголовок -> значение»:
#   1) заголовок: лист из списка известных заголовков (KNOWN_LABELS, аргумент known);
#   2) иначе: жирный лист, за которым идёт не жирный (новые, ещё неизвестные строки);
#   3) значение: все листья до следующего заголовка (кнопки и svg пропускаем).
# Если так ничего не нашлось, пробуем старую схему <li>. Из нескольких окон на странице
# берём то, где строк больше. Нет окна: [] (по этому _close понимает, что оно закрыто).
READ_ROWS_JS = r"""
(known) => {
    const knownSet = new Set(known.map(s => s.toLowerCase()));
    const clean = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
    const tidy = s => s.replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').replace(/@\s+/g, '@');
    const roots = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')];
    if (!roots.length) return [];

    const leaves = root => {
        const out = [];
        for (const el of root.querySelectorAll('*')) {
            if (el.children.length) continue;
            if (el.closest('svg, button, [role="button"], script, style')) continue;
            const t = clean(el.textContent);
            if (!t) continue;
            const w = parseInt(getComputedStyle(el).fontWeight, 10) || 400;
            out.push({t, bold: w >= 600});
        }
        return out;
    };

    const byLabels = root => {
        const items = leaves(root);
        const isLabel = (it, i) => {
            if (knownSet.has(it.t.toLowerCase())) return true;
            const nx = items[i + 1];
            return it.bold && it.t.length <= 40 && !!nx && !nx.bold && !knownSet.has(nx.t.toLowerCase());
        };
        const rows = [], seen = new Set();
        let label = null, parts = [];
        const flush = () => {
            if (label && parts.length && !seen.has(label)) {
                seen.add(label);
                rows.push([label, tidy(parts.join(' '))]);
            }
        };
        items.forEach((it, i) => {
            if (isLabel(it, i)) { flush(); label = it.t; parts = []; }
            else if (label) parts.push(it.t);
        });
        flush();
        return rows;
    };

    const byLi = root => {
        const rows = [], seen = new Set();
        for (const li of root.querySelectorAll('li')) {
            const texts = [];
            for (const el of li.querySelectorAll('div, span')) {
                if (el.children.length) continue;
                const t = clean(el.textContent);
                if (t) texts.push(t);
            }
            if (texts.length < 2 || seen.has(texts[0])) continue;
            seen.add(texts[0]);
            rows.push([texts[0], texts.slice(1).join(' ')]);
        }
        return rows;
    };

    let best = [];
    for (const root of roots) {
        let rows = byLabels(root);
        if (!rows.length) rows = byLi(root);
        if (rows.length >= best.length) best = rows;
    }
    return best;
}
"""

# Для диагностики: короткий текстовый снимок окна, когда строк не нашли.
DIALOG_TEXT_JS = r"""
() => [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')]
    .map(d => (d.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300))
"""


async def read_about_rows(page) -> list[list[str]]:
    return await page.evaluate(READ_ROWS_JS, list(KNOWN_LABELS))


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
        # роль menuitem у пункта может пропасть (в диагностике role=null): берём и по тексту
        item = (
            page.locator('[role="menuitem"]')
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
                log(
                    f"    [*] «···» #{n} (x={ic['x']:.0f}, y={ic['y']:.0f}, "
                    f"popup={ic['popup']}): пункта «Об этом профиле» нет"
                )
                await page.keyboard.press("Escape")  # не то меню: закрываем, пробуем следующий
                await page.wait_for_timeout(400)
                await page.evaluate("window.scrollTo(0, 0)")
                continue
            rows = await _wait_rows(page, 8000)
            snap: list[str] = []
            if not rows:  # снимок текста окна, пока оно открыто (после _close его уже нет)
                try:
                    snap = await page.evaluate(DIALOG_TEXT_JS)
                except PlaywrightError:
                    pass
            await _close(page, url)
            about = parse_about(rows)
            if about:
                return about
            log(f"    [!] «Об этом профиле»: окно открылось, но строк не нашли; текст окон: {snap}")
            return None
        log(
            f"    [!] «Об этом профиле»: пункт не нашли ни в одной из {min(len(icons), MAX_ICONS)} кнопок"
        )
    except PlaywrightError as e:
        log(f"    [!] «Об этом профиле»: ошибка браузера: {str(e)[:150]}")
    return None
