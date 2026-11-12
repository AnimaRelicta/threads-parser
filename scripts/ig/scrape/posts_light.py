"""--posts-only: лента профиля -> posts без открытия постов, комментариев и медиа."""

import asyncio
import random
from urllib.parse import quote

from core.logs import log
from core.timeutil import iso
from ig.consts import FEED_URL, JS_FETCH
from ig.db.store import IgStore
from ig.extract import private_from_text
from ig.feed import classify_feed, post_from_item
from ig.scrape.entry import fail_auth, fail_rate, open_profile, page_text
from ig.scrape.report import new_result
from scrape.anchor import anchor_covers, compute_anchor
from scrape.consts import OLD_STREAK_STOP

MAX_FEED_ERRORS = 2  # подряд ответов ленты без разбора, после чего профиль = error


async def run_posts_only(page, username: str, args, store: IgStore, account: str) -> dict:
    """Лента одного профиля -> posts (лёгкие); возвращает результат для summary и profile_runs.

    Остановка: end (лента кончилась) | old (дальше старше --since) | limit (--limit) |
    known (серия уже известных постов, только если якорь покрывает окно, не при --full)."""
    res = new_result(username, "posts")
    fail = await open_profile(page, username, account)
    if fail:
        res.update(fail)
        if fail["status"] == "not_found":
            await store.save_meta(username, "not_found", None, fail.get("reason"))
        return res

    known, anchor = await store.posts_context(username)
    cutoff, limit, stop_after = args.cutoff, args.limit, args.stop_after
    use_known = not args.full and anchor is not None and anchor_covers(anchor, cutoff)
    c = {"new": 0, "refreshed": 0, "unchanged": 0, "skipped_old": 0, "in_period": 0}
    handled: list[dict] = []
    stored_before = len(known)
    old_streak = known_streak = errors = pages = 0
    stop, cursor, seen = None, None, 0

    while stop is None:
        url = FEED_URL.format(username=username) + (f"&max_id={quote(cursor)}" if cursor else "")
        kind, data = classify_feed(await page.evaluate(JS_FETCH, url))
        pages += 1
        if kind == "rate":
            return _stopped(res, c, pages, fail_rate())
        if kind == "auth":
            note = f"API ленты отказал (401): сессия недействительна? ig accounts relogin {account}"
            return _stopped(res, c, pages, fail_auth(note))
        if kind == "forbidden":
            if private_from_text(await page_text(page)):
                res.update(status="private", note="закрытый профиль: лента недоступна")
                return _finish(res, c, pages, "end", handled, anchor, cutoff, False)
            return _stopped(res, c, pages, fail_auth("лента: 403 (сессия недействительна?)"))
        if kind != "page":
            errors += 1
            if errors >= MAX_FEED_ERRORS:
                res.update(status="error", note=f"лента не получена ({kind})")
                await store.mark_error(username)
                return _finish(res, c, pages, None, handled, anchor, cutoff, False)
            await asyncio.sleep(random.uniform(args.delay, args.delay * 1.5))
            continue
        errors = 0
        seen += len(data["items"])
        for item in data["items"]:
            post = post_from_item(item)
            if post is None:
                continue
            taken, pinned = post["taken_at"], post["is_pinned"]
            if cutoff and taken and taken < cutoff:
                c["skipped_old"] += 1  # старше окна: не пишем; закреплённый серию не считает
                if not pinned:
                    old_streak += 1
                    if old_streak >= OLD_STREAK_STOP:
                        stop = "old"
                        break
                continue
            was_known = post["code"] in known
            c[await store.save_post_light(username, post, known)] += 1
            c["in_period"] += 1
            handled.append(post)
            if pinned:
                continue
            old_streak = 0
            known_streak = known_streak + 1 if (was_known and use_known) else 0
            enough = stored_before + c["new"] >= args.min_posts
            if use_known and known_streak >= stop_after and enough:
                stop = "known"
                break
            if limit and c["in_period"] >= limit:
                stop = "limit"
                break
        if stop is None and not (data["more"] and data["next"]):
            stop = "end"
        elif stop is None:
            cursor = data["next"]
            await asyncio.sleep(random.uniform(args.delay, args.delay * 1.5))

    status = "ok" if seen else "empty"
    res.update(status=status)
    _finish(res, c, pages, stop, handled, anchor, cutoff, True)
    new_anchor = res.pop("anchor", None)
    if new_anchor:
        await store.save_anchor(username, new_anchor)
    return res


def _stopped(res: dict, c: dict, pages: int, fail: dict) -> dict:
    """Проблема аккаунта посреди ленты: уже записанные посты остаются, якорь не двигаем."""
    res.update(fail)
    res["posts"], res["pages_opened"] = _counts(c, []), pages
    return res


def _counts(c: dict, handled: list[dict]) -> dict:
    dates = [p["taken_at"] for p in handled if p["taken_at"]]
    return {**c, "newest": iso(max(dates).timestamp()) if dates else None}


def _finish(res, c, pages, stop, handled, anchor, cutoff, save: bool) -> dict:
    """Итог профиля; res["anchor"] — новый якорь (его записывает вызывающий, только после
    успешного прохода). Для private/error вызывается с save=False: ключа нет."""
    res.update(stop_reason=stop, posts=_counts(c, handled), pages_opened=pages)
    if save and stop:
        proc = [p["taken_at"] for p in handled if p["taken_at"]]
        posts = [{"code": p["code"], "date_utc": iso(p["taken_at"].timestamp())} for p in handled if p["taken_at"]]
        res["anchor"] = compute_anchor(anchor, stop, cutoff, proc, posts, {})
    log(
        f"[+] @{res['username']} | постов: новых {c['new']}, обновлено {c['refreshed']}, "
        f"без изменений {c['unchanged']}, старше окна {c['skipped_old']} | "
        f"остановка: {stop or '—'} | страниц: {pages}"
    )
    return res
