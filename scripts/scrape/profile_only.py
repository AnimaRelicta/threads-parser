"""Только профиль: метаданные страницы без обхода ленты и без сохранения веток.

Открывает профиль через `open_profile()` и на этом заканчивает: подписчики, био, «Об этом
профиле», шапка, связь с Instagram, страна/город. Ветки не читаются и не пишутся, вторая
вкладка не нужна. Формат результата как у `run_threads()`.
"""

from browser.net import Net
from core.logs import log
from db.profiles import ProfileStore
from scrape.entry import open_profile


async def run_profile_only(grid_page, net: Net, username: str, args, store: ProfileStore) -> dict:
    """Собирает метаданные одного профиля. Забаненные профили пропускаются."""
    result = {
        "username": username,
        "status": "ok",
        "saved": 0,
        "new_comments": 0,
        "fresh_posts": 0,
        "newest": None,
        "note": "только профиль",
        "mode": "profile",
        "abort_kind": None,
        "stop_reason": "end",
        "counts": {"refreshed": 0, "skipped_old": 0, "skipped_video": 0, "unchanged": 0},
    }
    pid = await store.ensure_profile(username)
    ban = await store.banned_reason(pid)
    if ban:
        log(f"[BAN] @{username} уже забанен: {ban}")
        result.update(status="banned", note=f"в бане: {ban}")
        return result
    _, fail = await open_profile(grid_page, net, username, pid, store)
    if fail:
        result.update(fail)
    return result
