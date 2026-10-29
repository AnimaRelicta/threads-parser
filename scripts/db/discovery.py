"""Новые профили по авторам: кандидаты из comments.author и posts.author_username.

Кандидат = ник, которого нет в profiles (сравнение по lower(username)):
- автор поста под веткой (comments.author) -> discovered_via = 'author'; профиль, под чьей
  веткой он найден, пишется в discovered_from_profile_id; автор, пишущий только под своей
  веткой, кандидатом не становится;
- автор ветки из поиска (posts.author_username при profile_id IS NULL) -> 'search'.
Если ник встретился в обоих местах, берётся 'search' (его ветка совпала с запросом), а
mentions_count суммируется. Новые профили: is_target = true, review_status = 'pending_review'.
Повторный запуск ничего не добавляет: заведённые ники исключаются, вставка идёт с
ON CONFLICT DO NOTHING, поэтому два одновременных запуска дублей тоже не создадут.
"""

from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from config import TH
from core.consts import USERNAME_RE

# $1 = нижняя граница периода (NULL = всё время), $2 = регулярка допустимого ника.
# Дата поста под веткой / ветки: posted_at / date_utc, а если их нет, момент сбора.
AUTHORS_SQL = """
SELECT DISTINCT ON (lower(c.author))
       c.author AS username,
       p.profile_id AS from_profile_id,
       c.post_code AS first_code,
       count(*) OVER (PARTITION BY lower(c.author)) AS hits
FROM comments c
JOIN posts p ON p.code = c.post_code
WHERE c.author ~ $2
  AND lower(c.author) <> lower(p.author_username)
  AND ($1::timestamptz IS NULL OR coalesce(c.posted_at, c.collected_at) >= $1)
  AND NOT EXISTS (SELECT 1 FROM profiles x WHERE lower(x.username) = lower(c.author))
ORDER BY lower(c.author), coalesce(c.posted_at, c.collected_at), c.id
"""

SEARCHED_SQL = """
SELECT DISTINCT ON (lower(p.author_username))
       p.author_username AS username,
       p.code AS first_code,
       count(*) OVER (PARTITION BY lower(p.author_username)) AS hits
FROM posts p
WHERE p.profile_id IS NULL
  AND p.author_username ~ $2
  AND ($1::timestamptz IS NULL OR coalesce(p.date_utc, p.created_at) >= $1)
  AND NOT EXISTS (SELECT 1 FROM profiles x WHERE lower(x.username) = lower(p.author_username))
ORDER BY lower(p.author_username), coalesce(p.date_utc, p.created_at), p.code
"""

INSERT_SQL = """
INSERT INTO profiles (
    username, url, is_target, discovered_via, discovered_from_profile_id,
    review_status, mentions_count, discovered_first_code)
SELECT t.u, $6 || '/@' || t.u, true, t.via, t.src, 'pending_review', t.m, t.code
FROM unnest($1::text[], $2::text[], $3::bigint[], $4::int[], $5::text[])
     AS t(u, via, src, m, code)
ON CONFLICT ((lower(username))) DO NOTHING
RETURNING discovered_via
"""


@dataclass
class Candidate:
    username: str
    via: str  # 'author' | 'search'
    from_profile_id: int | None
    first_code: str | None
    mentions: int


@dataclass
class DiscoveryResult:
    candidates: list[Candidate] = field(default_factory=list)
    added: dict[str, int] = field(default_factory=dict)  # discovered_via -> сколько вставлено


def merge_candidates(authors: list, searched: list) -> list[Candidate]:
    """Объединяет два списка по lower(ник); 'search' перекрывает 'author', счётчики суммируются."""
    found: dict[str, Candidate] = {}
    for r in authors:
        found[r["username"].lower()] = Candidate(
            r["username"], "author", r["from_profile_id"], r["first_code"], r["hits"]
        )
    for r in searched:
        key = r["username"].lower()
        prev = found.get(key)
        found[key] = Candidate(
            r["username"],
            "search",
            None,
            r["first_code"],
            r["hits"] + (prev.mentions if prev else 0),
        )
    return sorted(found.values(), key=lambda c: (-c.mentions, c.username.lower()))


async def find_candidates(
    conn: asyncpg.Connection, since: datetime | None, min_mentions: int = 1
) -> list[Candidate]:
    """Кандидаты за период since (None = всё время) с не менее чем min_mentions встреч."""
    authors = await conn.fetch(AUTHORS_SQL, since, USERNAME_RE.pattern)
    searched = await conn.fetch(SEARCHED_SQL, since, USERNAME_RE.pattern)
    return [c for c in merge_candidates(authors, searched) if c.mentions >= min_mentions]


async def insert_candidates(
    conn: asyncpg.Connection, candidates: list[Candidate]
) -> dict[str, int]:
    """Вставляет кандидатов в profiles; возвращает {discovered_via: сколько добавлено}."""
    if not candidates:
        return {}
    rows = await conn.fetch(
        INSERT_SQL,
        [c.username for c in candidates],
        [c.via for c in candidates],
        [c.from_profile_id for c in candidates],
        [c.mentions for c in candidates],
        [c.first_code for c in candidates],
        TH,
    )
    added: dict[str, int] = {}
    for r in rows:
        added[r["discovered_via"]] = added.get(r["discovered_via"], 0) + 1
    return added


async def discover_authors(
    conn: asyncpg.Connection,
    since: datetime | None,
    min_mentions: int = 1,
    dry_run: bool = False,
) -> DiscoveryResult:
    """Находит новых авторов и (если не dry_run) заводит их профилями одной транзакцией."""
    try:
        async with conn.transaction():
            result = DiscoveryResult(candidates=await find_candidates(conn, since, min_mentions))
            if not dry_run:
                result.added = await insert_candidates(conn, result.candidates)
    except asyncpg.UndefinedColumnError as e:
        raise SystemExit(
            f"В БД нет колонок поиска по авторам ({e}). "
            "Примените миграцию: uv run scripts/cli.py db migrate"
        ) from e
    return result
