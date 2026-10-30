"""Связь профиля с Instagram при открытии страницы Threads (без дополнительных запросов).

Порядок источников: ссылка/иконка Instagram в шапке (кнопка с href instagram.com/<ник>)
-> JSON страницы -> ссылка в био -> догадка «тот же ник» (confidence='guessed').
"""

import json

from playwright.async_api import Error as PlaywrightError

from core.header import Header
from core.instagram import IG_KEYS, IgLink, find_instagram_link


async def read_ig_json(page) -> list:
    """Embedded JSON страницы, где встречаются ключи с IG-ником (read_embedded их не берёт)."""
    try:
        scripts = await page.eval_on_selector_all(
            'script[type="application/json"]',
            "(els, keys) => els.map(e => e.textContent)"
            ".filter(t => t && keys.some(k => t.includes(k)))",
            list(IG_KEYS),
        )
    except PlaywrightError:
        return []
    docs = []
    for txt in scripts:
        try:
            docs.append(json.loads(txt))
        except (json.JSONDecodeError, TypeError):
            pass
    return docs


async def resolve_instagram(page, header: Header | None, bio: str | None, username: str) -> IgLink:
    """Лучшая из найденных связей профиля с Instagram (догадка, если ничего не нашли)."""
    ig = header.instagram if header else None
    if ig:
        return IgLink(
            username=ig["handle"],
            source="header_link",
            confidence="confirmed",
            matched_raw=ig["url"],
        )
    return find_instagram_link(await read_ig_json(page), bio, username)
