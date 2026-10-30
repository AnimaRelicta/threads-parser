"""Вход в профиль: страница ленты, проверки доступа, метаданные профиля.

Общий для полного прохода (`profile.py`) и быстрого сбора веток (`threads.py`).
"""

from browser.net import Net
from config import TH
from core.biography import biography_from_json
from core.followers import followers_from_json, followers_from_og
from core.logs import log
from db.profiles import ProfileStore
from scrape.about import fetch_about
from scrape.extract import read_embedded
from scrape.harvest import FeedIndex
from scrape.header import read_header
from scrape.instagram import resolve_instagram

META_JS = """() => ({
    title: document.title,
    og_title: document.querySelector('meta[property="og:title"]')?.content || null,
    og_description: document.querySelector('meta[property="og:description"]')?.content || null,
    og_image: document.querySelector('meta[property="og:image"]')?.content || null
})"""


async def open_profile(
    grid_page, net: Net, username: str, profile_id: int, store: ProfileStore
) -> tuple[FeedIndex | None, dict | None]:
    """Открывает ленту профиля и сохраняет его метаданные.

    Возвращает (FeedIndex, None) при успехе или (None, поля для result), если профиль
    недоступен: сессия недействительна, 404, 429.
    """
    net.grid_captured.clear()
    resp = await grid_page.goto(f"{TH}/@{username}", wait_until="domcontentloaded")
    await grid_page.wait_for_timeout(3000)
    if "/login" in grid_page.url:
        return None, {
            "status": "abort",
            "abort_kind": "auth",
            "note": "сессия недействительна: accounts.py relogin",
        }
    if resp is not None and resp.status == 404:
        return None, {"status": "not_found", "note": "профиль не найден"}
    if resp is not None and resp.status == 429:
        return None, {"status": "abort", "abort_kind": "429", "note": "429 Too Many Requests"}

    feed = FeedIndex()
    feed.feed(await read_embedded(grid_page))
    feed.update(net.grid_captured)

    meta = await grid_page.evaluate(META_JS)
    meta.update({"username": username, "url": f"{TH}/@{username}"})
    meta["followers"] = followers_from_og(meta.get("og_description"))
    if meta["followers"] is None:  # запасной путь: JSON страницы
        try:
            scripts = await grid_page.eval_on_selector_all(
                'script[type="application/json"]',
                "els => els.map(e => e.textContent).filter(t => t && t.includes('follower_count'))",
            )
            meta["followers"] = followers_from_json(scripts, username)
        except Exception:
            pass

    meta["bio"] = None
    try:
        bio_scripts = await grid_page.eval_on_selector_all(
            'script[type="application/json"]',
            "els => els.map(e => e.textContent).filter(t => t && t.includes('biography'))",
        )
        meta["bio"] = biography_from_json(bio_scripts, username)
    except Exception:
        pass

    await store.save_meta(profile_id, meta)

    # «···» → «Об этом профиле»: имя, дата регистрации, местоположение. Один раз на профиль
    # (about_fetched_at); не вышло — не фатально, повторится при следующем проходе.
    if await store.about_fetched(profile_id):
        about_note = "уже собран"
    else:
        about = await fetch_about(grid_page, meta["url"])
        if about:
            await store.save_about(profile_id, about)
            about_note = f"собран, место: {about.location or 'не указано'}"
        else:
            about_note = "НЕ удалось собрать"
    # Шапка: темы, ссылки в био, недавние просмотры (без кликов, при каждом заходе: просмотры меняются)
    header = await read_header(grid_page)
    if header:
        await store.save_header(profile_id, header)
        header_note = (
            f"просмотров: {header.recent_views if header.recent_views is not None else '?'}, "
            f"тем: {len(header.topics)}, ссылок: {len(header.links)}"
        )
    else:
        header_note = "шапка пустая"
    # Instagram: шапка > JSON страницы > био > догадка по нику (догадка помечена guessed)
    ig = await resolve_instagram(grid_page, header, meta.get("bio"), username)
    await store.save_instagram(profile_id, ig)
    # Страна/город: about_location > телефон WhatsApp > био/имя/темы. Только по уже собранному,
    # без запросов к Threads; сбой не должен ронять обход профиля.
    try:
        geo = await store.save_geo(profile_id)
        geo_note = (
            f"{geo.country}{' ' + geo.city if geo.city else ''} ({geo.source})"
            f"{' КОНФЛИКТ' if geo.conflict else ''}"
            if geo.country
            else "нет данных"
        )
    except Exception as e:
        geo_note = f"ошибка: {e!r}"
    log(
        f"[+] {meta.get('og_title') or meta.get('title')}"
        f" | подписчиков: {meta['followers'] if meta['followers'] is not None else '?'}"
        f" | об аккаунте: {about_note} | {header_note}"
        f" | instagram: @{ig.username} ({ig.source}{'?' if ig.confidence == 'guessed' else ''})"
        f" | гео: {geo_note}"
    )
    return feed, None
