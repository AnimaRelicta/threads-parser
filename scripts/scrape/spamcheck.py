"""Спам-проверка в ходе парсинга: по ленте профиля, до открытия веток."""

from core.logs import log
from core.spam import Verdict, analyze, dominant_author
from db.profiles import ProfileStore


def feed_items(infos) -> list[dict]:
    """Ветки из FeedIndex.items (dict code -> info) или списка info в формате analyze()."""
    rows = infos.values() if isinstance(infos, dict) else infos
    return [
        {
            "ts": i.get("ts"),
            "text": i.get("text"),
            "likes": i.get("likes"),
            "replies": i.get("replies"),
            "reposts": i.get("reposts"),
            "quotes": i.get("quotes"),
            "author_id": i.get("author_id"),
        }
        for i in rows
    ]


async def check_feed(
    store: ProfileStore, profile_id: int, username: str, infos, own_only: bool = False
) -> Verdict:
    """Считает вердикт по данным ленты и, если профиль спам, банит его в БД.

    own_only=True: в infos могут быть чужие ветки (JSON ленты содержит и рекомендации),
    оставляем самого частого автора. Меньше RULES.min_posts веток: ничего не пишем.
    """
    items = feed_items(infos)
    if own_only:
        items = dominant_author(items)
    rules = store.spam_rules
    v = analyze(items, rules, await store.followers_count(profile_id))
    if v.sample < rules.min_posts:
        return v
    banned = await store.save_spam_verdict(profile_id, v)
    if banned:
        log(f"[BAN] @{username}: {v.reason} (баллы {v.score})")
    elif v.metrics.get("trusted_by_followers"):
        log(f"[*] @{username}: {v.reason}")
    elif v.is_spam:
        log(f"[*] @{username} похож на спам, но в белом списке (баллы {v.score})")
    return v
