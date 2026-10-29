"""Обработка одного профиля."""

import asyncio
import random
from datetime import datetime
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from browser.account import Account
from browser.net import Net
from core.errors import AuthProblem, OldPost, RateLimited, SkipPost, ThreadAborted
from core.logs import log
from core.timeutil import iso, parse_dt
from db.profiles import ProfileStore
from scrape.anchor import DEFAULT_STOP_AFTER, anchor_covers, compute_anchor
from scrape.consts import OLD_STREAK_STOP, PINNED_LOOKAHEAD
from scrape.entry import open_profile
from scrape.feed import Lookahead, iter_post_links
from scrape.posts import (
    needs_refresh,
    refresh_comments,
    scrape_post,
    write_captions,
)
from scrape.report import media_totals


async def run_profile(
    acc: Account, grid_page, post_page, net: Net, username: str, args, store: ProfileStore
) -> dict:
    """Обрабатывает один профиль. Возвращает словарь с итогами.

    Состояние (посты, пропущенные, якорь) читается из Postgres и пишется туда же по мере работы."""
    cutoff = args.cutoff
    result = {
        "username": username,
        "status": "ok",
        "saved": 0,
        "new_comments": 0,
        "fresh_posts": 0,
        "newest": None,
        "note": "",
        "mode": "full",
        "abort_kind": None,
    }
    cnt = {"refreshed": 0, "skipped_old": 0, "skipped_video": 0, "unchanged": 0}

    out_dir = Path(args.output) / username
    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    state = await store.load(username)
    pid = state.profile_id
    posts: list[dict] = state.posts
    # Посты, у которых были медиа, но ни один файл не скачался, берём заново
    broken = [p["code"] for p in posts if p.get("media_failed", 0) > 0 and not p.get("media")]
    if broken:
        log(f"[*] Посты без файлов будут скачаны заново: {len(broken)}")
        posts = [p for p in posts if p["code"] not in broken]
    by_code = {p["code"]: p for p in posts}

    skipped = state.skipped

    feed, fail = await open_profile(grid_page, net, username, pid, store)
    if fail:
        result.update(fail)
        return result

    # Якорь: после первого прохода по окну лежит в profile_anchors; дальше обход
    # останавливается после N подряд известных постов (--full его игнорирует)
    anchor = None if getattr(args, "full", False) else state.anchor
    stop_after = 0
    if anchor is not None:
        known_codes = set(by_code) | set(skipped)
        if anchor["newest_code"] not in known_codes:
            log("[*] Якорь не найден среди сохранённых постов, полный проход")
            anchor = None
        elif not anchor_covers(anchor, cutoff):
            log("[*] Окно шире, чем при якоре, полный проход")
            anchor = None
        else:
            stop_after = max(1, getattr(args, "stop_after", DEFAULT_STOP_AFTER))
            result["mode"] = "only_new"
            log(
                f"[*] Режим «только новое»: стоп после {stop_after} подряд известных "
                f"постов (--full отключает)"
            )
    stop_reason = "end"  # end | old | limit | known: чем закончился обход
    known_streak = 0
    proc_dates: list[datetime] = []  # даты обработанных незакреплённых постов (для --limit)

    processed = 0  # сколько подходящих постов обработано (для --limit)
    old_streak = 0
    errors_in_row = 0
    found_any = False
    all_dates: list[datetime] = []

    def register_old() -> bool:
        """Учитывает старый пост. True, если пора остановиться."""
        nonlocal old_streak
        old_streak += 1
        return old_streak >= OLD_STREAK_STOP

    async def mark_skipped(shortcode: str, reason: str, date_str: str | None) -> None:
        skipped[shortcode] = {"reason": reason, "date": date_str}
        cnt["skipped_old" if reason == "old" else "skipped_video"] += 1
        await store.mark_skipped(pid, shortcode, reason, date_str)
        d = parse_dt(date_str)
        if d is not None:
            all_dates.append(d)

    links = Lookahead(iter_post_links(grid_page, args.with_videos, username))

    async def is_pinned(ts: int | None) -> bool:
        """Закреплённый пост стоит в начале ленты, хотя старше постов после него.
        Поэтому пост закреплён, если среди ближайших следующих есть более новый.
        На такие посты ограничение --since не распространяется."""
        if ts is None:
            return False
        for code, _ in await links.peek(PINNED_LOOKAHEAD):
            feed.update(net.grid_captured)
            t = (feed.items.get(code) or {}).get("ts")
            if t is not None and int(t) > int(ts):
                return True
        return False

    async def count_known(d: datetime | None) -> bool:
        """Известный пост, с которым делать нечего. True, если якорь велит остановиться.
        Закреплённые посты стоят в начале ленты и в серию не входят."""
        nonlocal known_streak, stop_reason
        if not stop_after:
            return False
        if d is not None and await is_pinned(int(d.timestamp())):
            return False
        known_streak += 1
        if known_streak >= stop_after:
            log(
                f"[*] {known_streak} известных постов подряд, дальше только знакомое: "
                "останавливаюсь (якорь; --full отключает)"
            )
            stop_reason = "known"
            return True
        return False

    async def note_processed(d: datetime | None) -> None:
        if args.limit and d is not None and not await is_pinned(int(d.timestamp())):
            proc_dates.append(d)

    async for shortcode, kind in links:
        feed.update(net.grid_captured)
        found_any = True
        finfo = feed.items.get(shortcode)
        existing = by_code.get(shortcode)

        # Пост, который мы уже видели раньше: без нужды не открываем
        if existing is not None or shortcode in skipped:
            d = (
                parse_dt(existing.get("date_utc"))
                if existing is not None
                else parse_dt(skipped[shortcode].get("date"))
            )
            if d is not None:
                all_dates.append(d)
            if (
                cutoff is not None
                and d is not None
                and d < cutoff
                and not await is_pinned(int(d.timestamp()))
            ):
                if register_old():
                    log("[*] Дальше только старые посты, останавливаюсь")
                    stop_reason = "old"
                    break
                if await count_known(d):
                    break
                continue
            if d is not None:
                old_streak = 0
            if existing is None:
                if await count_known(d):
                    break
                continue
            processed += 1
            await note_processed(d)
            if not needs_refresh(existing, finfo, args):
                log(f"[{processed}] {shortcode}: уже сохранён, пропуск")
                cnt["unchanged"] += 1
                if await count_known(d):
                    break
                if args.limit and processed >= args.limit:
                    stop_reason = "limit"
                    break
                continue
            known_streak = 0  # пост требует работы: серия известных прервана
            mode = "refresh"
            log(f"[{processed}] {shortcode}: проверяю новые комментарии")
        else:
            # Новый пост: пробуем отсеять по данным ленты, не открывая его
            if finfo is not None:
                ts = finfo.get("ts")
                if (
                    cutoff is not None
                    and ts is not None
                    and int(ts) < cutoff.timestamp()
                    and not await is_pinned(int(ts))
                ):
                    log(
                        f"[{processed + 1}] {shortcode}: старше периода "
                        f"({iso(ts)[:10]}), без открытия"
                    )
                    await mark_skipped(shortcode, "old", iso(ts))
                    if register_old():
                        log("[*] Дальше только старые посты, останавливаюсь")
                        stop_reason = "old"
                        break
                    continue
                if not args.with_videos and finfo.get("video"):
                    log(f"[{processed + 1}] {shortcode}: видео, без открытия")
                    await mark_skipped(shortcode, "video", iso(ts))
                    old_streak = 0
                    known_streak = 0
                    continue
            known_streak = 0
            mode = "new"
            log(f"[{processed + 1}] {shortcode}")

        try:
            await post_page.bring_to_front()
        except PlaywrightError:
            pass

        post = None
        try:
            if mode == "refresh":
                post = await refresh_comments(
                    acc, post_page, net, username, existing, media_dir, args
                )
            else:
                post = await scrape_post(
                    acc, post_page, net, username, shortcode, media_dir, args, exempt=is_pinned
                )
        except OldPost as e:
            date_str = e.args[0] if e.args else None
            log(f"    старше периода ({(date_str or '?')[:10]})")
            await mark_skipped(shortcode, "old", date_str)
            if register_old():
                log("[*] Дальше только старые посты, останавливаюсь")
                stop_reason = "old"
                break
            await asyncio.sleep(random.uniform(args.delay, args.delay * 2))
            continue
        except SkipPost as e:
            date_str = e.args[0] if e.args else None
            log("    видео, пропуск")
            await mark_skipped(shortcode, "video", date_str)
            old_streak = 0
            known_streak = 0
            await asyncio.sleep(random.uniform(args.delay, args.delay * 2))
            continue
        except ThreadAborted as e:
            # Частичные комментарии ценные: сохраняем и только потом останавливаемся
            post = e.post
            result["new_comments"] += post.pop("_stats_new", 0)
            if mode == "new":
                posts.append(post)
                by_code[shortcode] = post
                result["saved"] += 1
            await store.save_post(pid, username, post)
            if isinstance(e.cause, RateLimited):
                result.update(
                    status="abort", abort_kind="429", note="429 от Threads (комментарии не добраны)"
                )
                log("[!] 429 от Threads. Частичный результат сохранён, повторите позже.")
            else:
                result.update(status="abort", abort_kind="auth", note="checkpoint/авторизация")
                log("[!] Проблема с авторизацией. Частичный результат сохранён.")
            break
        except RateLimited:
            result.update(status="abort", abort_kind="429", note="429 от Threads")
            log("[!] 429 от Threads. Остановка, повторите позже.")
            break
        except AuthProblem as e:
            result.update(status="abort", abort_kind="auth", note="checkpoint/авторизация")
            log(
                f"[!] Проблема с авторизацией ({e}). "
                "Откройте Threads в браузере и подтвердите вход."
            )
            break
        except PlaywrightError as e:
            log(f"    [!] Ошибка браузера: {str(e)[:200]}")
            errors_in_row += 1
        else:
            errors_in_row = errors_in_row + 1 if post is None else 0

        if post:
            if mode == "refresh":
                cnt["refreshed"] += 1
            if mode == "new":
                posts.append(post)
                by_code[shortcode] = post
                processed += 1
                result["saved"] += 1
                d = parse_dt(post.get("date_utc"))
                if d is not None:
                    all_dates.append(d)
                await note_processed(d)
                old_streak = 0
            result["new_comments"] += post.pop("_stats_new", 0)
            await store.save_post(pid, username, post)

        if errors_in_row >= 5:
            result.update(status="abort", note="слишком много ошибок подряд")
            log("[!] Слишком много ошибок подряд, остановка.")
            break
        if args.limit and processed >= args.limit:
            stop_reason = "limit"
            break
        await asyncio.sleep(random.uniform(args.delay, args.delay * 2))

    # Читаемый список текстов: один раз за проход (раньше переписывался после каждого поста)
    write_captions(out_dir / "captions.txt", posts)

    # Всё ниже считается по всем постам профиля (из БД + новые), а не только за этот запуск
    result["media_posts"] = sum(1 for p in posts if p.get("media"))  # постов с медиа
    result["media"] = media_totals(posts)
    result["comments_total"] = sum(len(p.get("comments") or []) for p in posts)
    result["posts_total"] = len(posts)
    result["stop_reason"] = stop_reason
    result["counts"] = cnt
    if all_dates:
        result["newest"] = max(all_dates)
    result["fresh_posts"] = (
        len(all_dates) if cutoff is None else sum(1 for d in all_dates if d >= cutoff)
    )
    if not found_any and result["status"] == "ok":
        result.update(status="empty", note="посты не найдены (пустой или закрытый профиль)")
    elif cutoff is not None and result["fresh_posts"] == 0 and result["status"] == "ok":
        result["note"] = "за выбранный период постов нет"

    if result["status"] == "ok":
        new_anchor = compute_anchor(anchor, stop_reason, cutoff, proc_dates, posts, skipped)
        if new_anchor is not None:
            await store.save_anchor(pid, new_anchor)
    if stop_reason == "known":
        result["note"] = (result["note"] + "; " if result["note"] else "") + "стоп по якорю"
    return result
