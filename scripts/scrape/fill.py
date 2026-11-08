"""Разбор веток, собранных быстрым режимом: открыть ветку, сохранить текст, медиа, посты под ней.

Вместо обхода ленты берёт из БД ветки профиля без `last_refreshed_at` и разбирает каждую
теми же `scrape_post()` и `save_post()`, что и полный проход. Якорь не пишется.
"""

import asyncio
import random
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from browser.account import Account
from browser.net import Net
from core.errors import AuthProblem, OldPost, RateLimited, SkipPost, ThreadAborted
from core.logs import log
from db.profiles import ProfileStore
from scrape.posts import scrape_post
from scrape.report import new_counts, new_result

MAX_ERRORS_IN_ROW = 5


async def _period_already_applied(_ts: int) -> bool:
    """Период применён при сборе веток: здесь ветку за --since не отбрасываем."""
    return True


async def run_fill(
    acc: Account, post_page, net: Net, username: str, args, store: ProfileStore
) -> dict:
    """Разбирает неразобранные ветки профиля за период. Формат результата как у run_profile()."""
    result = new_result(username, "fill")
    cnt = new_counts()
    pid = await store.ensure_profile(username)
    pending = await store.pending_threads(pid, args.cutoff, args.limit or None)
    if not pending:
        result.update(status="empty", note="нет неразобранных веток за период (--threads-only)")
        result.update(counts=cnt, stop_reason="end")
        return result
    log(f"[*] Неразобранных веток: {len(pending)}")

    media_dir = Path(args.output) / username / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    stop_reason, errors_in_row = "end", 0
    dates = []

    for i, item in enumerate(pending, start=1):
        code = item["code"]
        log(f"[{i}/{len(pending)}] {code}")
        try:
            await post_page.bring_to_front()
        except PlaywrightError:
            pass
        post = None
        try:
            post = await scrape_post(
                acc, post_page, net, username, code, media_dir, args, exempt=_period_already_applied
            )
        except OldPost:  # недостижимо при exempt, но страхуемся от бесконечного повтора
            await store.mark_skipped(pid, code, "old", None)
            cnt["skipped_old"] += 1
            continue
        except SkipPost as e:
            log("    видео, пропуск")
            await store.mark_skipped(pid, code, "video", e.args[0] if e.args else None)
            cnt["skipped_video"] += 1
            errors_in_row = 0
            await asyncio.sleep(random.uniform(args.delay, args.delay * 2))
            continue
        except ThreadAborted as e:
            post = e.post  # частичные комментарии ценные: сохраняем и останавливаемся
            result["new_comments"] += post.pop("_stats_new", 0)
            result["saved"] += 1
            await store.save_post(pid, username, post)
            if isinstance(e.cause, RateLimited):
                result.update(status="abort", abort_kind="429", note="429 от Threads")
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
            log(f"[!] Проблема с авторизацией ({e}).")
            break
        except PlaywrightError as e:
            log(f"    [!] Ошибка браузера: {str(e)[:200]}")
            errors_in_row += 1
        else:
            errors_in_row = errors_in_row + 1 if post is None else 0

        if post:
            result["saved"] += 1
            result["new_comments"] += post.pop("_stats_new", 0)
            await store.save_post(pid, username, post)
            if item["date"] is not None:
                dates.append(item["date"])
        if errors_in_row >= MAX_ERRORS_IN_ROW:
            result.update(status="abort", note="слишком много ошибок подряд")
            log("[!] Слишком много ошибок подряд, остановка.")
            break
        await asyncio.sleep(random.uniform(args.delay, args.delay * 2))

    result.update(counts=cnt, stop_reason=stop_reason, fresh_posts=len(dates))
    result["posts_total"] = result["saved"]
    result["newest"] = max(dates) if dates else None
    return result
