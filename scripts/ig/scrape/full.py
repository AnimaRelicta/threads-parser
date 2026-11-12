"""Полный проход: лента + комментарии и медиа по флагам (посты не открываются отдельно:
адреса медиа и счётчики берутся из элементов ленты)."""

from core.logs import log
from ig.db.store import IgStore
from ig.scrape.post_extras import Extras, save_avatar
from ig.scrape.posts_light import run_feed
from ig.storage import ensure_space


def summary_line(res: dict, extras: Extras) -> str:
    m = extras.media
    return (
        f"    комментариев новых: {extras.comments['new']} | файлов: {m['new_files']} "
        f"(фото {m['new_images']}, видео {m['new_videos']}, {m['new_bytes'] / 1e6:.1f} МБ), "
        f"ошибок загрузки: {m['download_failed']}"
    )


async def run_full(page, username: str, args, store: IgStore, account: str) -> dict:
    """Полный проход одного профиля -> результат с блоками posts/comments/media."""
    if args.images or args.videos:
        ensure_space(args.storage_dir, args.min_free_gb)
    extras = Extras(page, username, args, store)
    await extras.load()
    res = await run_feed(page, username, args, store, account, mode="full", extras=extras.handle)
    res["comments"], res["media"] = extras.comments, extras.media
    if args.avatars and res["status"] in ("ok", "empty"):
        await save_avatar(page, username, args, store)
    if res["status"] in ("ok", "empty", "abort"):
        log(summary_line(res, extras))
    return res
