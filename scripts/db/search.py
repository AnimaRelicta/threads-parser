"""Поиск: очередь задач (search_jobs), найденные посты и связи post_hits."""

from datetime import date

import asyncpg

from core.textfilter import FILTER_VERSION, classify, hashtags_mentions
from core.timeutil import parse_dt
from db.posts import upsert_topic


async def ensure_job(
    conn: asyncpg.Connection,
    key: str,
    query: str,
    mode: str,
    after: date | None,
    before: date | None,
) -> tuple[int, str]:
    """(id, статус) задачи; заводит строку pending, если такой задачи ещё не было."""
    row = await conn.fetchrow(
        """
        INSERT INTO search_jobs (job_key, query, mode, after_date, before_date)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (job_key) DO UPDATE SET job_key = EXCLUDED.job_key
        RETURNING id, status
        """,
        key,
        query,
        mode,
        after,
        before,
    )
    return row["id"], row["status"]


async def start_job(conn: asyncpg.Connection, job_id: int, run_id: int, account: str) -> None:
    await conn.execute(
        """
        UPDATE search_jobs SET status = 'running', run_id = $2, account = $3,
               started_at = now(), finished_at = NULL, last_error = NULL
        WHERE id = $1
        """,
        job_id,
        run_id,
        account,
    )


async def finish_job(
    conn: asyncpg.Connection, job_id: int, found: int, new: int, dupes: int
) -> None:
    await conn.execute(
        """
        UPDATE search_jobs SET status = 'done', finished_at = now(), last_error = NULL,
               posts_found = $2, posts_new = $3, posts_dupes = $4
        WHERE id = $1
        """,
        job_id,
        found,
        new,
        dupes,
    )


async def fail_job(conn: asyncpg.Connection, job_id: int, error: str, retries: int) -> None:
    """Попытки исчерпаны: не done, следующий запуск (без --force) возьмёт задачу снова."""
    await conn.execute(
        """
        UPDATE search_jobs SET status = 'failed', finished_at = now(),
               last_error = $2, retries = $3
        WHERE id = $1
        """,
        job_id,
        error,
        retries,
    )


async def requeue_job(
    conn: asyncpg.Connection, job_id: int, error: str | None, count_retry: bool
) -> None:
    """Задача вернулась в очередь (429 или упавшая сессия аккаунта)."""
    await conn.execute(
        """
        UPDATE search_jobs SET status = 'pending', last_error = $2,
               retries = retries + $3
        WHERE id = $1
        """,
        job_id,
        error,
        1 if count_retry else 0,
    )


async def release_running_jobs(conn: asyncpg.Connection, run_id: int) -> int:
    """После обрыва запуска задачи в статусе running возвращаются в pending."""
    res = await conn.execute(
        "UPDATE search_jobs SET status = 'pending' WHERE run_id = $1 AND status = 'running'",
        run_id,
    )
    return int(res.split()[-1])


# Найденный поиском пост не перезаписывает уже сохранённый (в том числе собранный профилем).
# last_refreshed_at остаётся NULL: по нему профильный пайплайн отличает свои посты.
SEARCH_POST_SQL = """
INSERT INTO posts (
    code, url, author_username, author_user_id, profile_id, text, topic_id, date_utc,
    likes, reply_count, hashtags, mentions,
    filter_status, filter_matched, filter_matched_in, filter_version, first_seen_via)
VALUES ($1, $2, $3, $4, (SELECT id FROM profiles WHERE lower(username) = lower($3)),
        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'search')
ON CONFLICT (code) DO NOTHING
RETURNING code
"""


def normalize_keyword(query: str) -> str:
    """Ключ для posts.found_keywords: пробелы схлопнуты, нижний регистр."""
    return " ".join(query.split()).lower()


async def add_keyword(conn: asyncpg.Connection, codes: list[str], keyword: str) -> None:
    """Дописывает запрос в posts.found_keywords постов (если его там ещё нет)."""
    if not codes or not keyword:
        return
    await conn.execute(
        """
        UPDATE posts SET found_keywords = array_append(found_keywords, $2)
        WHERE code = ANY($1::text[]) AND NOT ($2 = ANY(found_keywords))
        """,
        codes,
        keyword,
    )


async def save_hits(
    conn: asyncpg.Connection, job_id: int, posts: list[dict]
) -> tuple[int, int]:
    """Сохраняет посты задачи и связи post_hits. Возвращает (новых, повторов).

    Повтор = пост уже был в базе (найден другим запросом или собран профилем); связь с
    задачей добавляется всё равно: по ней считается качество каждого запроса, а запрос
    задачи дописывается в posts.found_keywords (и у новых постов, и у повторов).
    """
    new = dupes = 0
    topic_ids: dict[str, int] = {}
    async with conn.transaction():
        for p in posts:
            topic = p.get("topic")
            topic_id = None
            if topic:
                if topic.lower() not in topic_ids:
                    topic_ids[topic.lower()] = await upsert_topic(conn, topic)
                topic_id = topic_ids[topic.lower()]
            status, matched, matched_in = classify(p.get("text"), topic)
            tags, mentions = hashtags_mentions(p.get("text"))
            inserted = await conn.fetchval(
                SEARCH_POST_SQL,
                p["code"],
                p.get("url"),
                p["username"],
                p.get("user_id"),
                p.get("text"),
                topic_id,
                parse_dt(p.get("date_utc")),
                p.get("likes"),
                p.get("reply_count"),
                tags,
                mentions,
                status,
                matched,
                matched_in,
                FILTER_VERSION,
            )
            if inserted:
                new += 1
            else:
                dupes += 1
        if posts:
            await conn.executemany(
                "INSERT INTO post_hits (post_code, job_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                [(p["code"], job_id) for p in posts],
            )
            query = await conn.fetchval("SELECT query FROM search_jobs WHERE id = $1", job_id)
            await add_keyword(conn, [p["code"] for p in posts], normalize_keyword(query or ""))
    return new, dupes


async def refilter(conn: asyncpg.Connection, everything: bool = False) -> dict:
    """Пересчитывает filter_* по текущим правилам.

    По умолчанию берутся посты со старой версией правил, everything=True пересчитывает все.
    """
    rows = await conn.fetch(
        """
        SELECT p.code, p.text, p.filter_status, t.name AS topic
        FROM posts p LEFT JOIN topics t ON t.id = p.topic_id
        WHERE $1::boolean OR p.filter_version <> $2
        """,
        everything,
        FILTER_VERSION,
    )
    updates, changed = [], 0
    for r in rows:
        status, matched, matched_in = classify(r["text"], r["topic"])
        changed += status != r["filter_status"]
        updates.append((r["code"], status, matched, matched_in, FILTER_VERSION))
    if updates:
        await conn.executemany(
            """
            UPDATE posts SET filter_status = $2, filter_matched = $3, filter_matched_in = $4,
                   filter_version = $5, updated_at = now()
            WHERE code = $1
            """,
            updates,
        )
    return {"checked": len(rows), "changed": changed}
