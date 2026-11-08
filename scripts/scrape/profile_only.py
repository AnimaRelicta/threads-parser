"""Только профиль: метаданные страницы без обхода ленты и без сохранения веток.

Открывает профиль через `open_profile()` и на этом заканчивает: подписчики, био, «Об этом
профиле», шапка, связь с Instagram, страна/город. Ветки не читаются и не пишутся, вторая
вкладка не нужна. Формат результата как у `run_threads()`.
"""

from browser.net import Net
from db.profiles import ProfileStore
from scrape.entry import open_profile
from scrape.report import new_counts, new_result


async def run_profile_only(grid_page, net: Net, username: str, args, store: ProfileStore) -> dict:
    """Собирает метаданные одного профиля."""
    result = new_result(
        username, "profile", note="только профиль", stop_reason="end", counts=new_counts()
    )
    pid = await store.ensure_profile(username)
    _, fail = await open_profile(grid_page, net, username, pid, store)
    if fail:
        result.update(fail)
    return result
