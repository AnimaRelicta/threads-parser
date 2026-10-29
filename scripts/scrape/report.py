"""Итоги запуска: счётчики, записи журнала, сводка."""

from datetime import datetime

from core.logs import log

MODE_LABELS = {"only_new": "только новое", "threads": "только список веток"}


def media_totals(posts: list[dict]) -> dict:
    """Сколько медиа лежит в файле профиля: у постов и в комментариях (их там большинство)."""
    in_posts = [m for p in posts for m in p.get("media") or []]
    comments = [c for p in posts for c in p.get("comments") or []]
    in_comments = [m for c in comments for m in c.get("media") or []]
    allm = in_posts + in_comments
    return {
        "total_files": len(allm),
        "in_posts": len(in_posts),
        "in_comments": len(in_comments),
        "posts_with_media": sum(1 for p in posts if p.get("media")),
        "comments_with_media": sum(1 for c in comments if c.get("media")),
        "images": sum(1 for m in allm if m.get("type") != "video"),
        "videos": sum(1 for m in allm if m.get("type") == "video"),
        "failed": sum(p.get("media_failed", 0) for p in posts)
        + sum(c.get("media_failed", 0) for c in comments),
    }


def profile_run_entry(res: dict) -> dict:
    """Запись о запуске профиля для реестра: всё, что уникально для этого прогона.

    Плоский результат профиля (res) остаётся в summary.json; здесь та же информация
    сгруппирована так, чтобы потом ложиться в БД: вложенные блоки posts/comments/media.
    """
    cnt, ps, md = res.get("counts") or {}, res.get("pstat") or {}, res.get("media") or {}
    newest = res.get("newest")
    return {
        "username": res.get("username"),
        "status": res.get("status"),  # ok | empty | not_found | abort | error
        "mode": res.get("mode"),  # full | only_new | threads
        "stop_reason": res.get("stop_reason"),  # end | old | limit | known
        "abort_kind": res.get("abort_kind"),  # 429 | auth | None
        "note": res.get("note") or "",
        "started_at": res.get("started_at"),
        "finished_at": res.get("finished_at"),
        "duration_sec": res.get("duration_sec"),
        "posts": {
            "new": res.get("saved", 0),  # сохранено в этом запуске
            "refreshed": cnt.get("refreshed", 0),  # досмотрены ради новых комментариев
            "unchanged": cnt.get("unchanged", 0),  # известны, открывать не пришлось
            "skipped_old": cnt.get("skipped_old", 0),
            "skipped_video": cnt.get("skipped_video", 0),
            "in_period": res.get("fresh_posts", 0),
            "in_file": res.get("posts_total", 0),
            "newest": newest.isoformat() if hasattr(newest, "isoformat") else newest,
        },
        "comments": {
            "new": res.get("new_comments", 0),
            "in_file": res.get("comments_total", 0),
        },
        "media": {
            "new_files": ps.get("files", 0),  # скачано в этом запуске
            "new_images": ps.get("images", 0),
            "new_videos": ps.get("videos", 0),
            "new_bytes": ps.get("bytes", 0),
            "download_failed": ps.get("failed", 0),
            "in_file": md.get("total_files", 0),  # всего у профиля: посты + комментарии
            "in_posts": md.get("in_posts", 0),
            "in_comments": md.get("in_comments", 0),
            "posts_with_media": md.get("posts_with_media", 0),
            "comments_with_media": md.get("comments_with_media", 0),
        },
        "pages_opened": ps.get("pages_opened", 0),  # страниц ответов, открытых браузером
    }


def print_summary(results: list[dict], cutoff: datetime | None) -> None:
    log("\n" + "=" * 60)
    log("ИТОГИ" + (f" (период: с {cutoff:%Y-%m-%d})" if cutoff else ""))
    log("=" * 60)
    for r in results:
        newest = f"{r['newest']:%Y-%m-%d}" if r["newest"] else "—"
        line = (
            f"@{r['username']}: {r['status']} | новых постов: {r['saved']} | "
            f"новых комментариев: {r.get('new_comments', 0)} | "
            f"постов в периоде: {r['fresh_posts']} | "
            f"медиа всего: {(r.get('media') or {}).get('total_files', 0)} "
            f"(посты {(r.get('media') or {}).get('in_posts', 0)}, "
            f"комментарии {(r.get('media') or {}).get('in_comments', 0)}) | "
            f"последний пост: {newest} | "
            f"режим: {MODE_LABELS.get(r.get('mode'), 'полный проход')}"
        )
        if r["note"]:
            line += f" | {r['note']}"
        log(line)
