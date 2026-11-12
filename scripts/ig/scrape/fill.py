"""--fill: добор постов, собранных --posts-only (ленту не листаем, посты берём по media_id)."""

import asyncio
import random

from core.errors import AuthProblem, RateLimited
from core.logs import log
from ig.consts import JS_FETCH, MEDIA_INFO_URL
from ig.db.store import IgStore
from ig.feed import classify_error, post_from_item
from ig.scrape.entry import fail_auth, fail_rate
from ig.scrape.full import summary_line
from ig.scrape.post_extras import Extras, save_avatar
from ig.scrape.posts_light import open_checked
from ig.scrape.report import new_result
from ig.storage import ensure_space


async def run_fill(page, username: str, args, store: IgStore, account: str) -> dict:
    """Посты профиля без полной обработки -> комментарии/медиа по флагам, поля поста обновляются."""
    res = new_result(username, "fill", stop_reason="end")
    if args.images or args.videos:
        ensure_space(args.storage_dir, args.min_free_gb)
    if not await open_checked(page, username, account, store, res):
        return res
    extras = Extras(page, username, args, store)
    await extras.load()
    todo = await store.posts_to_fill(username, args.comments, args.videos)
    done = failed = 0
    try:
        for row in todo:
            data = await page.evaluate(JS_FETCH, MEDIA_INFO_URL.format(media_id=row["media_id"]))
            bad = classify_error(data)
            if bad == "rate":
                raise RateLimited()
            if bad == "auth":
                raise AuthProblem("сессия недействительна (401) при чтении поста")
            items = (data or {}).get("items") if not bad else None
            post = post_from_item(items[0]) if items else None
            if post is None:
                failed += 1
                log(f"    [!] {row['code']}: пост не получен ({bad or 'пусто'})")
                continue
            post["media_id"] = row["media_id"]
            await store.update_post_fields(post)
            await extras.handle(post, items[0], True)
            done += 1
            await asyncio.sleep(random.uniform(args.delay, args.delay * 1.5))
    except RateLimited:
        res.update(fail_rate())
    except AuthProblem as e:
        res.update(fail_auth(str(e)))
    res["posts"] = {"refreshed": done}
    res["comments"], res["media"] = extras.comments, extras.media
    if failed:
        res["note"] = f"постов не получено: {failed}"
    if res["status"] == "abort":
        return res
    if args.avatars:
        await save_avatar(page, username, args, store)
    res["status"] = "ok"
    log(f"[+] @{username} | добрано постов: {done} из {len(todo)}")
    log(summary_line(res, extras))
    return res
