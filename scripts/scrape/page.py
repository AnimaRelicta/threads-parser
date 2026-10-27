"""Работа со страницей: ожидание сети, прокрутка, «ещё», открытие поста."""

from playwright.async_api import Error as PlaywrightError

from browser.net import Net
from config import TH
from core.errors import AuthProblem, RateLimited
from core.jsonwalk import walk
from scrape.consts import SORT_QS
from scrape.extract import read_embedded
from scrape.harvest import Harvester


async def settle(
    page,
    live: list,
    until=None,
    min_ms: int = 0,
    quiet_ms: int = 700,
    max_ms: int = 6000,
    step_ms: int = 150,
) -> None:
    """Ждёт по факту: выходит, когда until() истинно либо перестали приходить
    новые ответы сервера (quiet_ms тишины после min_ms), но не дольше max_ms."""
    waited = quiet = 0
    last = len(live)
    while waited < max_ms:
        await page.wait_for_timeout(step_ms)
        waited += step_ms
        cur = len(live)
        if cur != last:
            last, quiet = cur, 0
            if until is not None and until():
                return
        else:
            quiet += step_ms
        if waited >= min_ms and quiet >= quiet_ms:
            return


CLICK_MORE_JS = r"""
() => {
    let count = 0;
    const patterns = [
        /^view\s+(more|all)/i, /^load\s+more/i, /^show\s+more/i,
        /more\s+replies/i, /view\s+\d+\s+more/i, /load\s+\d+\s+more/i,
        /показать\s+(еще|ещё|все|больше)/i, /загрузить\s+еще/i, /еще\s+ответ/i
    ];
    const els = document.querySelectorAll(
        'button, span[role="button"], div[role="button"], a[role="button"]');
    for (const el of els) {
        const text = (el.textContent || '').trim();
        if (!text || text.length > 60) continue;
        if (!patterns.some(p => p.test(text))) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.top < window.innerHeight * 2) {
            try { el.click(); count++; } catch (e) {}
        }
    }
    return count;
}
"""


SCROLL_JS = r"""
() => {
    // Прокручиваем окно и все внутренние прокручиваемые блоки до низа
    const moved = [];
    const doc = document.scrollingElement || document.documentElement;
    const before = doc.scrollTop;
    window.scrollTo(0, doc.scrollHeight);
    moved.push(doc.scrollTop - before);
    for (const el of document.querySelectorAll('div, main, section')) {
        if (el.scrollHeight <= el.clientHeight + 50) continue;
        const oy = getComputedStyle(el).overflowY;
        if (oy !== 'auto' && oy !== 'scroll') continue;
        const b = el.scrollTop;
        el.scrollTop = el.scrollHeight;
        moved.push(el.scrollTop - b);
    }
    // Последняя ссылка на пост в зоне видимости запускает ленивую подгрузку
    const links = document.querySelectorAll('a[href*="/post/"]');
    if (links.length) {
        try { links[links.length - 1].scrollIntoView({block: 'end'}); } catch (e) {}
    }
    return {moved: moved.some(v => v > 0), y: Math.round(doc.scrollTop),
            h: doc.scrollHeight, inner: window.innerHeight,
            containers: moved.length - 1};
}
"""


async def scroll_down(page) -> dict:
    """Прокрутка вниз: окно + внутренние блоки + колесо мыши (на случай,
    когда подгрузка слушает именно wheel-события). Возвращает метрики."""
    info = await page.evaluate(SCROLL_JS) or {}
    try:
        await page.mouse.move(640, 450)
        await page.mouse.wheel(0, 1500)
    except PlaywrightError:
        pass
    return info


def page_infos(docs: list) -> list[tuple]:
    """(has_next_page, есть ли end_cursor) из page_info в новых ответах."""
    out = []
    for doc in docs:
        for d in walk(doc):
            pi = d.get("page_info")
            if isinstance(pi, dict) and "has_next_page" in pi:
                out.append((pi.get("has_next_page"), bool(pi.get("end_cursor"))))
    return out


async def click_more(page) -> int:
    try:
        return int(await page.evaluate(CLICK_MORE_JS) or 0)
    except PlaywrightError:
        return 0


async def open_node(page, net: Net, username: str, code: str) -> Harvester:
    """Открывает пост/ответ и возвращает накопитель его JSON."""
    net.captured.clear()
    resp = await page.goto(f"{TH}/@{username}/post/{code}{SORT_QS}", wait_until="domcontentloaded")
    if resp is not None and resp.status == 429:
        raise RateLimited()
    if "/login" in page.url:
        raise AuthProblem(page.url)
    h = Harvester()
    h.feed(await read_embedded(page))
    h.feed_new(net.captured)
    return h


async def load_root(page, net: Net, username: str, shortcode: str):
    """Открывает пост и ждёт, пока в данных появится он сам."""
    h = await open_node(page, net, username, shortcode)
    if shortcode not in h.pool:
        await settle(
            page,
            net.captured,
            until=lambda: (h.feed_new(net.captured), shortcode in h.pool)[1],
            quiet_ms=1500,
            max_ms=8000,
        )
    return h, h.pool.get(shortcode)
