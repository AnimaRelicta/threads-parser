"""Спам-проверка в ходе парсинга: по ленте профиля, до открытия веток."""

from core.logs import log
from core.spam import Verdict, analyze, dominant_author
from core.spamclass import CLASS_TITLES, ClassVerdict, classify
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


async def check_class(
    store: ProfileStore, profile_id: int, username: str, items: list[dict], followers: int | None
) -> ClassVerdict:
    """Класс 1/2/3 по первым веткам ленты (core/spamclass.py); бан не выставляет.

    Проверка идёт на каждой пачке веток: в БД пишем всегда (метрики уточняются), в лог только
    при смене класса."""
    cv = classify(items, followers, store.class_rules)
    if cv.no_data:
        return cv
    await store.save_spam_class(profile_id, cv)
    if store.last_classes.get(profile_id, "new") != cv.code:
        store.last_classes[profile_id] = cv.code
        if cv.code:
            log(f"[CLASS {cv.code}] @{username}: {CLASS_TITLES[cv.code]} ({cv.reason})")
    return cv


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
    followers = await store.followers_count(profile_id)
    await check_class(store, profile_id, username, items, followers)
    v = analyze(items, rules, followers)
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
