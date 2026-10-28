"""Разовый перенос старых файлов профилей (posts.json и др.) в Postgres."""

from datetime import date
from pathlib import Path

import asyncpg

from core.consts import USERNAME_RE
from core.fsio import read_json, read_jsonl
from core.timeutil import parse_dt
from db import posts as repo
from db import search as search_repo
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


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


async def import_search(conn: asyncpg.Connection, posts_path: Path, jobs_path: Path) -> dict:
    """Переносит jobs.json и posts.jsonl поиска.

    Задачи берутся как есть (статус, счётчики). В posts.jsonl у поста записан только первый
    нашедший запрос и режим, без окна дат, поэтому связи post_hits идут к служебной задаче
    «legacy|запрос|режим» (status done). Реальные задачи с окнами она не затрагивает.
    """
    res = {"jobs": 0, "jobs_kept": 0, "posts": 0, "posts_kept": 0}
    jobs = read_json(jobs_path, {})
    if isinstance(jobs, dict):
        for key, r in jobs.items():
            if not isinstance(r, dict) or not r.get("query"):
                continue
            parts = key.rsplit("|", 3)
            after, before = (_date(parts[2]), _date(parts[3])) if len(parts) == 4 else (None, None)
            status = r.get("status") if r.get("status") in ("done", "failed") else "failed"
            out = await conn.execute(
                """
                INSERT INTO search_jobs (job_key, query, mode, after_date, before_date, status,
                    finished_at, posts_found, posts_new, posts_dupes, last_error)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (job_key) DO NOTHING
                """,
                key,
                r["query"],
                r.get("mode") if r.get("mode") in ("top", "recent") else "recent",
                after,
                before,
                status,
                parse_dt(r.get("finished_at")),
                r.get("posts") or 0,
                r.get("new_posts") or 0,
                r.get("duplicates") or 0,
                r.get("last_error"),
            )
            res["jobs" if out.endswith(" 1") else "jobs_kept"] += 1

    groups: dict[tuple[str, str], list[dict]] = {}
    for p in read_jsonl(posts_path):
        if p.get("code") and p.get("username"):
            mode = p.get("mode") if p.get("mode") in ("top", "recent") else "recent"
            groups.setdefault((p.get("query") or "", mode), []).append(p)
    for (query, mode), group in groups.items():
        job_id = await conn.fetchval(
            """
            INSERT INTO search_jobs (job_key, query, mode, status, finished_at)
            VALUES ($1, $2, $3, 'done', now())
            ON CONFLICT (job_key) DO UPDATE SET job_key = EXCLUDED.job_key
            RETURNING id
            """,
            f"legacy|{query.lower()}|{mode}",
            query,
            mode,
        )
        new, dupes = await search_repo.save_hits(conn, job_id, group)
        await conn.execute(
            """
            UPDATE search_jobs SET posts_found = posts_found + $2, posts_new = posts_new + $3,
                   posts_dupes = posts_dupes + $4 WHERE id = $1
            """,
            job_id,
            len(group),
            new,
            dupes,
        )
        res["posts"] += new
        res["posts_kept"] += dupes
    return res
