#!/usr/bin/env python3
"""Диагностика «Об этом профиле»: почему парсер не видит кнопку/окно с датой регистрации и гео.

    uv run scripts/diagnose_about.py account_002 --url https://www.threads.com/@davrenbekov.e
    uv run scripts/diagnose_about.py account_002 --url ... --headless

Повторяет сценарий scrape/about.py по шагам и на каждом пишет, что увидел:
  0. константы кода (MAX_ICONS = 0 -> ни одна кнопка не пробуется вообще);
  1. страница: статус, редирект на логин, сообщение об ошибке/ограничении, язык интерфейса;
  2. кнопки «···»: все svg-заголовки страницы, сколько найдено, сколько осталось после
     фильтра левой панели, их координаты и aria-haspopup;
  3. клик по каждой кнопке: какие пункты меню открылись, есть ли «Об этом профиле»
     (в т. ч. в другом написании/языке);
  4. окно: сколько строк прочитал READ_ROWS_JS и что разобрал parse_about.
В конце — вывод (VERDICT). Всё пишется в data/diagnostics/<время>/: report.md, скриншоты,
html найденных элементов. Папку (или хотя бы report.md) пришлите целиком.
Ничего не пишет в БД и не меняет куки.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import Error as PlaywrightError  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from browser.launch import launch_browser, new_context  # noqa: E402
from browser.session import load_cookies  # noqa: E402
from config import ROOT, TH, load_settings  # noqa: E402
from core.about import parse_about  # noqa: E402
from scrape import about as ab  # noqa: E402

# Все svg с заголовком + кнопки с aria-label: на случай, если Threads переименовал «Ещё».
PAGE_FACTS_JS = r"""
() => {
    const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 60);
    const titles = {};
    for (const svg of document.querySelectorAll('svg')) {
        const t = svg.querySelector(':scope > title');
        const name = clean((t && t.textContent) || svg.getAttribute('aria-label'));
        if (name) titles[name] = (titles[name] || 0) + 1;
    }
    const arias = {};
    for (const el of document.querySelectorAll('[role="button"][aria-label], button[aria-label]')) {
        const a = clean(el.getAttribute('aria-label'));
        if (a) arias[a] = (arias[a] || 0) + 1;
    }
    return {
        title: document.title,
        lang: document.documentElement.lang,
        url: location.href,
        svg_titles: titles,
        button_aria: arias,
        body_text: clean(document.body.innerText).slice(0, 300),
        has_login_form: !!document.querySelector('input[name="username"], input[type="password"]'),
        dialogs: document.querySelectorAll('[role="dialog"]').length,
    };
}
"""

# Что открылось после клика по «···»: пункты меню и любые элементы с текстом «профил».
MENU_FACTS_JS = r"""
() => {
    const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    const items = [...document.querySelectorAll('[role="menuitem"], [role="menu"] [role="button"]')]
        .map(e => clean(e.textContent)).filter(Boolean);
    const hits = [...document.querySelectorAll('span, div, a')]
        .filter(e => !e.children.length && /об этом профиле|about this profile|about this account|профил/i.test(e.textContent || ''))
        .slice(0, 8).map(e => ({
            text: clean(e.textContent), role: e.getAttribute('role'),
            parent_role: e.parentElement && e.parentElement.getAttribute('role'),
            visible: !!(e.getClientRects().length)}));
    return {menus: document.querySelectorAll('[role="menu"]').length, items, hits,
            dialogs: document.querySelectorAll('[role="dialog"]').length};
}
"""

# Сколько «заголовочных» span в окне: если 0, сменилась вёрстка (жирность/wbloks_1).
DIALOG_FACTS_JS = r"""
() => {
    const dialogs = document.querySelectorAll('[role="dialog"]');
    const root = dialogs.length ? dialogs[dialogs.length - 1] : document;
    const bold = [...root.querySelectorAll('span')].filter(s =>
        !s.children.length && parseInt(getComputedStyle(s).fontWeight, 10) >= 600 && s.textContent.trim());
    return {
        dialogs: dialogs.length,
        wbloks: root.querySelectorAll('.wbloks_1').length,
        wbloks_spans: root.querySelectorAll('.wbloks_1 span').length,
        bold_spans: bold.slice(0, 15).map(s => ({
            text: s.textContent.trim().slice(0, 50),
            in_wbloks: !!s.closest('.wbloks_1')})),
        text: (root.innerText || '').replace(/\s+/g, ' ').slice(0, 500),
        html: root === document ? '' : root.outerHTML.slice(0, 6000),
    };
}
"""


class Report:
    def __init__(self, out: Path):
        self.out = out
        self.lines: list[str] = []
        self.problems: list[str] = []

    def say(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def block(self, title: str, data) -> None:
        body = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
        self.say(f"\n### {title}\n```\n{body}\n```")

    def problem(self, text: str) -> None:
        self.problems.append(text)
        self.say(f"[ПРОБЛЕМА] {text}")

    def save(self) -> None:
        (self.out / "report.md").write_text("\n".join(self.lines), encoding="utf-8")


async def shot(page, rep: Report, name: str) -> None:
    try:
        await page.screenshot(path=str(rep.out / f"{name}.png"))
    except PlaywrightError:
        pass


async def run(args) -> None:
    st = load_settings()
    data = load_cookies(st.cookies_path(args.account))
    if not data:
        raise SystemExit(f"Нет кук аккаунта «{args.account}» ({st.cookies_path(args.account)})")

    out = ROOT / "data" / "diagnostics" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rep = Report(out)
    rep.say(f"# Диагностика «Об этом профиле», аккаунт {args.account}\n- профиль: {args.url}")

    # --- шаг 0: константы кода --------------------------------------------------------
    rep.say("\n## Шаг 0. Константы scrape/about.py")
    rep.say(f"- MAX_ICONS = {ab.MAX_ICONS}, SIDEBAR_SHARE = {ab.SIDEBAR_SHARE}")
    if ab.MAX_ICONS < 1:
        rep.problem(
            "MAX_ICONS < 1: цикл `for ... in icons[:MAX_ICONS]` не выполняется ни разу, поэтому "
            "кнопки не нажимаются, даже если они есть на странице. Поставьте MAX_ICONS = 3."
        )

    async with async_playwright() as p:
        browser = await launch_browser(p, args.headless)
        try:
            ctx = await new_context(browser, data)
            page = await ctx.new_page()

            # --- шаг 1: страница профиля ---------------------------------------------
            rep.say("\n## Шаг 1. Страница профиля")
            resp = await page.goto(args.url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            status = resp.status if resp else None
            rep.say(f"- HTTP: {status}, итоговый URL: {page.url}")
            await shot(page, rep, "1_profile")
            if status == 429:
                rep.problem("429: аккаунт/IP ограничен, страница профиля не отдаётся нормально")
            if "/login" in page.url:
                rep.problem("редирект на /login: куки аккаунта протухли (accounts.py relogin)")
            facts = await page.evaluate(PAGE_FACTS_JS)
            rep.block("Факты страницы (язык, svg-заголовки, aria-label кнопок)", facts)
            if facts["has_login_form"]:
                rep.problem("на странице форма входа: сессия не авторизована")
            if facts["lang"] and not facts["lang"].lower().startswith(("ru", "en")):
                rep.problem(
                    f"язык интерфейса «{facts['lang']}»: заголовок svg и пункт меню могут называться "
                    "иначе, чем «Ещё»/«More» и «Об этом профиле»/«About this profile»"
                )

            # --- шаг 2: поиск кнопок «···» -------------------------------------------
            rep.say("\n## Шаг 2. Кнопки «···»")
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(300)
            raw = await page.evaluate(ab.FIND_ICONS_JS)
            rep.block("Все найденные svg «Ещё»/«More» (до фильтра левой панели)", raw)
            icons = await ab._icons(page)
            rep.say(f"- найдено: {len(raw)}, после фильтра левой панели: {len(icons)}")
            if not raw:
                alt = {k: v for k, v in facts["svg_titles"].items() if k not in ("", None)}
                rep.problem(
                    "ни одного svg с заголовком «Ещё»/«More»: Threads переименовал значок или "
                    f"страница не догрузилась. Все svg-заголовки на странице: {alt}"
                )
            elif not icons:
                rep.problem(
                    f"все {len(raw)} кнопок отсеяны как «левая панель» (x < {ab.SIDEBAR_SHARE} "
                    "ширины окна): возможно, изменилась вёрстка или размер окна"
                )

            # --- шаг 3: клик по каждой кнопке ----------------------------------------
            rep.say("\n## Шаг 3. Клик по кнопкам и меню")
            opened = False
            for n, ic in enumerate(icons[: args.max_icons], 1):
                rep.say(f"\n#### Кнопка #{n}: x={ic['x']:.0f}, y={ic['y']:.0f}, popup={ic['popup']}")
                await page.mouse.click(ic["x"], ic["y"])
                await page.wait_for_timeout(1200)
                await shot(page, rep, f"3_menu_{n}")
                menu = await page.evaluate(MENU_FACTS_JS)
                rep.block(f"Меню после клика #{n}", menu)
                item = page.locator('[role="menuitem"]').filter(has_text=ab.MENU_ITEM).first
                try:
                    await item.wait_for(state="visible", timeout=3000)
                except PlaywrightError:
                    rep.say(f"- пункта «Об этом профиле» нет (меню: {menu['items']})")
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(400)
                    await page.evaluate("window.scrollTo(0, 0)")
                    continue
                rep.say("- пункт «Об этом профиле» найден, нажимаю")
                await item.click(timeout=3000)
                opened = True

                # --- шаг 4: окно ------------------------------------------------------
                rep.say("\n## Шаг 4. Окно «Об этом профиле»")
                rows: list = []
                for _ in range(32):  # до 8 сек, как _wait_rows
                    rows = await ab.read_about_rows(page)
                    if rows:
                        break
                    await page.wait_for_timeout(250)
                await shot(page, rep, "4_dialog")
                dlg = await page.evaluate(DIALOG_FACTS_JS)
                (out / "dialog.html").write_text(dlg.pop("html", ""), encoding="utf-8")
                rep.block("Строки, прочитанные READ_ROWS_JS", rows)
                rep.block("Устройство окна (жирные span, wbloks)", dlg)
                about = parse_about(rows)
                rep.block(
                    "Результат parse_about",
                    {"name": about.name, "joined": about.joined, "location": about.location,
                     "raw": about.raw},
                )
                if not dlg["dialogs"]:
                    rep.problem("после клика по пункту нет [role=dialog]: окно не открылось")
                elif not rows:
                    rep.problem(
                        "окно открылось, но READ_ROWS_JS не нашёл строк: сменилась вёрстка "
                        f"(wbloks_1: {dlg['wbloks']}, жирных span: {len(dlg['bold_spans'])}); "
                        "см. dialog.html и 4_dialog.png"
                    )
                elif not (about.joined or about.location):
                    rep.problem(
                        f"строки прочитаны ({list(about.raw)}), но заголовки не распознаны "
                        "core/about.py (_LABELS): добавьте новые названия"
                    )
                break

            if icons and not opened:
                rep.problem(
                    f"{len(icons)} кнопок «···» найдено, но «Об этом профиле» не появилось ни в "
                    "одном меню: у профиля пункта нет (свой профиль/бизнес/ограничение) или он "
                    "переименован — смотрите «Меню после клика» и скриншоты 3_menu_*.png"
                )
        finally:
            await browser.close()

    rep.say("\n## VERDICT")
    if rep.problems:
        for i, t in enumerate(rep.problems, 1):
            rep.say(f"{i}. {t}")
    else:
        rep.say("Проблем не найдено: кнопка, пункт меню и окно работают. Если в парсере всё равно "
                "не собирается — проверьте на проблемном профиле (--url) и на другом аккаунте.")
    rep.save()
    print(f"\nОтчёт: {out / 'report.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("account", help="имя аккаунта из data/cookies/<имя>.json")
    ap.add_argument("--url", required=True, help="профиль, на котором не собирается about")
    ap.add_argument("--headless", action="store_true", help="без окна браузера (по умолчанию с окном)")
    ap.add_argument("--max-icons", type=int, default=5, help="сколько кнопок «···» пробовать")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
