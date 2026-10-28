"""Разовый перенос старых файлов профилей (posts.json и др.) в Postgres."""

from pathlib import Path

import asyncpg

from core.consts import USERNAME_RE
from core.fsio import read_json
from db import posts as repo
from db.runs import ensure_profile

# Старые posts.json хранили shortcode/caption/comments_count; приводим к code/text/reply_count
LEGACY_POST_KEYS = {"shortcode": "code", "caption": "text", "comments_count": "reply_count"}


def normalize_post(p: dict) -> dict:
    for old, new in LEGACY_POST_KEYS.items():
        if old in p:
            val = p.pop(old)
            p.setdefault(new, val)
    return p


async def import_profile_dir(conn: asyncpg.Connection, d: Path, root: Path) -> dict | None:
    """Переносит одну папку профиля. Посты, уже собранные в БД, не перезаписываются."""
    raw = read_json(d / "posts.json", None)
    if not isinstance(raw, list):
        return None
    username = d.name
    pid = await ensure_profile(conn, username)
    counts = {"posts": 0, "comments": 0, "skipped": 0, "anchor": 0, "kept": 0}

    meta = read_json(d / "profile.json", None)
    if isinstance(meta, dict):
        await repo.save_meta(conn, pid, meta)

    have = {
        r["code"]
        for r in await conn.fetch(
            "SELECT code FROM posts WHERE profile_id = $1 AND last_refreshed_at IS NOT NULL", pid
        )
    }
    for p in raw:
        if not isinstance(p, dict):
            continue
        p = normalize_post(p)
        if not p.get("code"):
            continue
        if p["code"] in have:
            counts["kept"] += 1
            continue
        await repo.save_post(conn, pid, username, p, root)
        counts["posts"] += 1
        counts["comments"] += len(p.get("comments") or [])

    skipped = read_json(d / "skipped.json", {})
    if isinstance(skipped, list):
        skipped = {c: {"reason": "video", "date": None} for c in skipped}
    if isinstance(skipped, dict):
        rows = await conn.fetch("SELECT code FROM skipped_posts WHERE profile_id = $1", pid)
        known = {r["code"] for r in rows}
        for code, v in skipped.items():
            if code in known or not isinstance(v, dict):
                continue
            reason = v.get("reason") if v.get("reason") in ("old", "video") else "video"
            await repo.mark_skipped(conn, pid, code, reason, v.get("date"))
            counts["skipped"] += 1

    anchor = read_json(d / "anchor.json", None)
    if isinstance(anchor, dict) and anchor.get("newest_code"):
        if await repo.load_anchor(conn, pid) is None:
            await repo.save_anchor(conn, pid, anchor)
            counts["anchor"] = 1
    return counts


async def import_profiles(
    conn: asyncpg.Connection, root: Path, only: list[str] | None = None
) -> dict[str, dict]:
    """{профиль: счётчики} по папкам root/<ник>/ с posts.json."""
    wanted = {n.lower() for n in only} if only else None
    out: dict[str, dict] = {}
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not USERNAME_RE.match(d.name):
            continue
        if wanted is not None and d.name.lower() not in wanted:
            continue
        counts = await import_profile_dir(conn, d, root)
        if counts is not None:
            out[d.name] = counts
    return out
