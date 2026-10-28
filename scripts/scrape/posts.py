"""Сбор и обновление одного поста; читаемый captions.txt."""

import asyncio
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from browser.account import Account
from browser.net import Net
from config import TH
from core.errors import OldPost, SkipPost, ThreadAborted
from core.logs import log
from core.timeutil import iso, parse_dt
from scrape.comments import collect_thread, finalize_comments, root_node_from
from scrape.extract import caption_of, is_video_obj, media_urls, topic_of
from scrape.media import download
from scrape.page import load_root


def comment_lines(shortcode: str, comments: list[dict]) -> list[str]:
    """Комментарии деревом для читаемого файла."""
    by_parent: dict[str, list[dict]] = {}
    for c in comments:
        by_parent.setdefault(str(c.get("parent_id")), []).append(c)
    for lst in by_parent.values():
        lst.sort(key=lambda c: c.get("created_at") or "")

    lines: list[str] = []
    emitted: set[str] = set()

    def emit(parent_id: str, level: int) -> None:
        for c in by_parent.get(parent_id, []):
            if c["id"] in emitted:
                continue
            emitted.add(c["id"])
            text = (c.get("text") or "(без текста)").replace("\n", " ")
            tag = f" +{len(c['media'])} фото" if c.get("media") else ""
            lines.append(
                "    " * level + f"↳ @{c.get('author')} "
                f"[{(c.get('created_at') or '?')[:16]}]: {text}{tag}"
            )
            emit(c["id"], level + 1)

    emit(shortcode, 1)
    for c in comments:  # на случай «осиротевших» ответов
        if c["id"] not in emitted:
            emit(str(c.get("parent_id")), 1)
            if c["id"] not in emitted:
                emitted.add(c["id"])
                lines.append(f"    ↳ @{c.get('author')}: {(c.get('text') or '(без текста)')[:200]}")
    return lines


def write_captions(path: Path, posts: list[dict]) -> None:
    """Читаемый текстовый файл с текстами постов и деревом комментариев."""
    parts = []
    for p in posts:
        block = [f"{p.get('date_utc') or '?'} | {p['url']}", p.get("text") or "(без текста)"]
        if p.get("comments"):
            block.append("")
            block.extend(comment_lines(p["code"], p["comments"]))
        parts.append("\n".join(block) + "\n")
    path.write_text("\n" + ("\n" + "-" * 60 + "\n\n").join(parts), encoding="utf-8")


async def scrape_post(
    acc: Account, page, net: Net, username: str, shortcode: str, media_dir: Path, args, exempt=None
) -> dict | None:
    """Новый пост: текст, медиа и (по флагу) дерево комментариев."""
    h, media = await load_root(page, net, username, shortcode)

    if args.debug:
        dbg = Path(args.output) / "debug"
        dbg.mkdir(parents=True, exist_ok=True)
        (dbg / f"{shortcode}.json").write_text(
            json.dumps(
                {
                    "captured_responses": len(net.captured),
                    "objects_in_pool": len(h.pool),
                    "media_found": media is not None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if media is None:
            (dbg / f"{shortcode}.html").write_text(await page.content(), encoding="utf-8")

    if media is None:
        log("    [!] Данные поста не найдены в ответах (запустите с --debug)")
        return None

    caption = caption_of(media)
    ts = media.get("taken_at")
    cutoff = getattr(args, "cutoff", None)
    if cutoff is not None and ts is not None and int(ts) < cutoff.timestamp():
        if not (exempt is not None and await exempt(int(ts))):
            raise OldPost(iso(ts))
    date_tag = (
        datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d") if ts else "unknown"
    )

    items = media_urls(media)
    videos_skipped = 0
    if not args.with_videos:
        if is_video_obj(media):
            raise SkipPost(iso(ts))
        images = [(k, u) for k, u in items if k == "image"]
        videos_skipped = len(items) - len(images)
        items = images

    files = []
    for i, (mkind, url) in enumerate(items, start=1):
        name = await download(acc, url, media_dir / f"{date_tag}_{shortcode}_{i}", mkind)
        if name:
            files.append({"type": mkind, "file": name})
        await asyncio.sleep(random.uniform(0.3, 0.9))

    if items and not files:
        log("    [!] Ни один файл не скачан, пост не сохраняю (повторится при следующем запуске)")
        return None

    info = media.get("text_post_app_info") or {}
    post = {
        "code": shortcode,
        "url": f"{TH}/@{username}/post/{shortcode}",
        "date_utc": iso(ts),
        "text": caption,
        "topic": topic_of(media),
        "hashtags": re.findall(r"#\w+", caption or ""),
        "mentions": re.findall(r"@[\w.]+", caption or ""),
        "likes": media.get("like_count"),
        "reply_count": info.get("direct_reply_count"),
        "reposts": info.get("repost_count"),
        "quotes": info.get("quote_count"),
        "videos_skipped": videos_skipped,
        "media": files,
        "media_failed": len(items) - len(files),
        "comments": [],
        "visited_reply_count": None,
        "comments_depth": 0,
    }

    if args.comments:
        root = root_node_from(media, username, shortcode)
        comments, stats = await collect_thread(acc, page, net, h, root, [], media_dir, args)
        finalize_comments(post, root, comments, stats, args)
        if stats["error"] is not None:
            raise ThreadAborted(post, stats["error"])
    return post


async def refresh_comments(
    acc: Account, page, net: Net, username: str, post: dict, media_dir: Path, args
) -> dict | None:
    """Уже сохранённый пост: докачивает только новые комментарии."""
    shortcode = post["code"]
    h, media = await load_root(page, net, username, shortcode)
    if media is None:
        log("    [!] Пост не открылся (удалён или скрыт), пропускаю")
        return None

    info = media.get("text_post_app_info") or {}
    post["likes"] = media.get("like_count", post.get("likes"))
    post["reply_count"] = info.get("direct_reply_count")
    post["reposts"] = info.get("repost_count")
    post["quotes"] = info.get("quote_count")

    root = root_node_from(media, username, shortcode)
    comments, stats = await collect_thread(
        acc,
        page,
        net,
        h,
        root,
        post.get("comments", []),
        media_dir,
        args,
        deep=is_fresh(post, args),
        root_visited=post.get("visited_reply_count") is not None,
    )
    finalize_comments(post, root, comments, stats, args)
    if stats["error"] is not None:
        raise ThreadAborted(post, stats["error"])
    return post


def is_fresh(post: dict, args) -> bool:
    """Пост моложе --recheck-days: под ним ещё активно комментируют."""
    if args.recheck_days <= 0:
        return False
    d = parse_dt(post.get("date_utc"))
    return d is not None and datetime.now(timezone.utc) - d < timedelta(days=args.recheck_days)


def needs_refresh(post: dict, finfo: dict | None, args) -> bool:
    """Нужно ли заново открыть уже сохранённый пост ради новых комментариев."""
    if not args.comments:
        return False
    if post.get("comments_depth", 0) < args.depth:
        return True  # комментарии ещё не собирались или нужна большая глубина
    visited = post.get("visited_reply_count")
    if visited is None:
        return True  # прошлый обход не был завершён
    if is_fresh(post, args):
        return True  # свежий пост: новые ответы могут быть на любой глубине
    replies = finfo.get("replies") if finfo else None
    if replies is not None:
        return replies > visited  # по ленте видно, что ответов стало больше
    return False
