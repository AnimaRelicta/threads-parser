"""Быстрый сбор веток профиля: только лента, сами ветки не открываются.

Данные берутся из JSON ленты (`FeedIndex`): текст, дата, счётчики. Посты под ветками,
медиа и якорь `profile_anchors` не трогаются: ветки остаются «неразобранными»
(`posts.last_refreshed_at IS NULL`), полный проход потом заберёт их сам.
"""

import asyncio
from datetime import datetime, timezone

from browser.net import Net
from core.logs import log
from core.timeutil import iso
from db.profiles import ProfileStore
from scrape.consts import OLD_STREAK_STOP, PINNED_LOOKAHEAD
from scrape.entry import open_profile
from scrape.feed import Lookahead, iter_post_links

FLUSH_EVERY = 10  # сколько веток копим перед записью в БД
NO_DATA_WAIT_MS = 1500  # ждём JSON ленты, если ссылка уже есть, а данных ещё нет


async def run_threads(grid_page, net: Net, username: str, args, store: ProfileStore) -> dict:
    """Собирает ветки одного профиля за период. Формат результата как у run_profile()."""
    cutoff = args.cutoff
    result = {
        "username": username,
        "status": "ok",
        "saved": 0,
        "new_comments": 0,
        "fresh_posts": 0,
        "newest": None,
        "note": "",
        "mode": "threads",
        "abort_kind": None,
    }
    cnt = {"refreshed": 0, "skipped_old": 0, "skipped_video": 0, "unchanged": 0}

    pid = await store.ensure_profile(username)
    feed, fail = await open_profile(grid_page, net, username, pid, store)
    if fail:
        result.update(fail)
        return result

    full = bool(getattr(args, "full", False))
    stop_after = 0 if full else max(1, getattr(args, "stop_after", 1))
    known = await store.known_codes(pid)
    links = Lookahead(iter_post_links(grid_page, True, username))

    async def is_pinned(ts: int) -> bool:
        """Закреплённая ветка стоит в начале ленты, хотя старше следующих за ней."""
        for code, _ in await links.peek(PINNED_LOOKAHEAD):
            feed.update(net.grid_captured)
            t = (feed.items.get(code) or {}).get("ts")
            if t is not None and int(t) > int(ts):
                return True
        return False

    batch: list[dict] = []

    async def flush() -> None:
        if batch:
            await store.save_threads_light(pid, username, batch)
            batch.clear()

    stop_reason = "end"  # end | old | limit | known
    old_streak = known_streak = taken = no_data = seen = 0
    dates: list[datetime] = []
    found_any = False

    async for code, _ in links:
        found_any = True
        feed.update(net.grid_captured)
        info = feed.items.get(code)
        if info is None:  # JSON ленты может прийти чуть позже ссылки
            await grid_page.wait_for_timeout(NO_DATA_WAIT_MS)
            feed.update(net.grid_captured)
            info = feed.items.get(code)
        if info is None:
            no_data += 1
            log(f"    {code}: нет данных в ленте, пропуск")
            continue
        seen += 1
        ts = int(info["ts"])
        is_known = code in known
        need_pin = (cutoff is not None and ts < cutoff.timestamp()) or is_known or args.limit
        pinned = await is_pinned(ts) if need_pin else False

        if cutoff is not None and ts < cutoff.timestamp() and not pinned:
            cnt["skipped_old"] += 1
            old_streak += 1
            if old_streak >= OLD_STREAK_STOP:
                log("[*] Дальше только старые ветки, останавливаюсь")
                stop_reason = "old"
                break
            continue
        old_streak = 0

        dates.append(datetime.fromtimestamp(ts, tz=timezone.utc))
        batch.append({"code": code, **info})
        if is_known:
            cnt["unchanged"] += 1
            if stop_after and not pinned:
                known_streak += 1
        else:
            result["saved"] += 1
            known_streak = 0
        log(
            f"[{len(dates)}] {code}: {(iso(ts) or '')[:10]}, ответов {info.get('replies')}"
            + (" (известна)" if is_known else "")
        )
        if len(batch) >= FLUSH_EVERY:
            await flush()
        if stop_after and known_streak >= stop_after:
            log(f"[*] {known_streak} известных веток подряд: останавливаюсь (--full отключает)")
            stop_reason = "known"
            break
        if args.limit and not pinned:
            taken += 1
            if taken >= args.limit:
                stop_reason = "limit"
                break
        await asyncio.sleep(0)  # отдаём управление другим воркерам
    await flush()

    result["counts"] = cnt
    result["stop_reason"] = stop_reason
    result["posts_total"] = len(dates)
    result["fresh_posts"] = len(dates)
    result["newest"] = max(dates) if dates else None
    if not found_any:
        result.update(status="empty", note="ветки не найдены (пустой или закрытый профиль)")
    elif not dates:
        result["note"] = "за выбранный период веток нет"
    if no_data:
        result["note"] = (result["note"] + "; " if result["note"] else "") + (
            f"без данных в ленте: {no_data}"
        )
    return result
