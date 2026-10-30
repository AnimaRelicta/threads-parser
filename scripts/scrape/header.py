"""Шапка профиля: темы (чипы-ссылки), ссылки в био, «недавние просмотры».

Читается из DOM уже открытой страницы профиля, без кликов. Шапкой считается всё, что лежит
выше первого поста ленты (по записи кликов: чипы тем — a[href*=serp_type=tags], просмотры —
div[role=button] «N недавних просмотров», ссылки — a[target=_blank], иконка Instagram — тоже
ссылка). Темы постов в ленте ниже границы и в шапку не попадают.
"""

from playwright.async_api import Error as PlaywrightError

from core.header import Header, parse_header
from core.logs import log

NO_POSTS_LIMIT = 700  # профиль без постов: шапка = верхние 700 px (ниже подвал сайта)

READ_HEADER_JS = r"""
(noPostsLimit) => {
    const posts = [...document.querySelectorAll('a[href*="/post/"]')]
        .map(a => a.getBoundingClientRect()).filter(r => r.width > 0);
    const limit = posts.length ? Math.min(...posts.map(r => r.top + scrollY)) : noPostsLimit;
    const inHeader = el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.bottom + scrollY <= limit + 2;
    };
    const clean = s => (s || '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
    const topics = [], links = [];
    let views = null;
    for (const a of document.querySelectorAll('a[href]')) {
        if (!inHeader(a)) continue;
        const href = a.getAttribute('href');
        if (href.includes('serp_type=tags') && href.includes('tag_id=')) {
            topics.push({text: clean(a.textContent), href});
            continue;
        }
        let host = '';
        try { host = new URL(a.href).hostname; } catch (e) { continue; }
        if (!/^https?:/.test(a.href) || /^(www\.)?threads\.(com|net)$/.test(host)) continue;
        const t = a.querySelector('svg > title');
        links.push({text: clean(a.textContent), href: a.href, icon: t ? clean(t.textContent) : null});
    }
    for (const b of document.querySelectorAll('[role="button"]')) {
        if (!inHeader(b)) continue;
        const t = clean(b.textContent);
        if (t.length < 60 && /недавн\S*\s+просмотр|recent\s+views?/i.test(t)) { views = t; break; }
    }
    return {views, topics, links};
}
"""


async def read_header(page) -> Header | None:
    """Темы, ссылки и просмотры открытого профиля; None, если шапка пустая или сбой браузера."""
    try:
        header = parse_header(await page.evaluate(READ_HEADER_JS, NO_POSTS_LIMIT))
    except PlaywrightError as e:
        log(f"    [!] шапка профиля: ошибка браузера: {str(e)[:150]}")
        return None
    return header if header else None
