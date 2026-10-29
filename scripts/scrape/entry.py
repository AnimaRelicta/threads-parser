"""Вход в профиль: страница ленты, проверки доступа, метаданные профиля.

Общий для полного прохода (`profile.py`) и быстрого сбора веток (`threads.py`).
"""

from browser.net import Net
from config import TH
from core.logs import log
from db.profiles import ProfileStore
from scrape.extract import read_embedded
from scrape.harvest import FeedIndex

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
    await store.save_meta(profile_id, meta)
    log(f"[+] {meta.get('og_title') or meta.get('title')}")
    return feed, None
