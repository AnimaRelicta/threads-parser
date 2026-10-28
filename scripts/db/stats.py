"""Статистика из БД: реестр отчётов (SQL + заголовок) и вывод таблицей.

Новый отчёт = одна запись в REPORTS; команды (accounts.py list, search.py --stats и др.)
берут отчёты отсюда по имени и ничего не знают про SQL.
"""

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class Report:
    name: str
    title: str
    sql: str
    note: str = ""  # пояснение под таблицей


REPORTS: dict[str, Report] = {}


def register(report: Report) -> Report:
    REPORTS[report.name] = report
    return report


# Аккаунты: замена счётчиков accounts.json. Запуски и ошибки считаются по run_accounts,
# профили/посты/комментарии/медиа по profile_runs (подзапросы, чтобы суммы не множились).
register(
    Report(
        name="accounts",
        title="Аккаунты",
        sql="""
            SELECT a.name,
                   CASE WHEN a.username IS NOT NULL THEN '@' || a.username
                        ELSE 'id:' || coalesce(a.user_id, '?') END AS "user",
                   a.status,
                   coalesce(ra.runs, 0)        AS runs,
                   coalesce(pr.profiles, 0)    AS profiles,
                   coalesce(pr.posts, 0)       AS posts,
                   coalesce(pr.comments, 0)    AS comments,
                   coalesce(pr.media_files, 0) AS media,
                   coalesce(ra.e429, 0)        AS "429",
                   coalesce(ra.eauth, 0)       AS auth,
                   coalesce(to_char(a.last_run_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), '-')
                                               AS last_run,
                   left(coalesce(a.last_error, ''), 30) AS error
            FROM accounts a
            LEFT JOIN (
                SELECT account, count(*) AS runs,
                       sum(errors_429) AS e429, sum(errors_auth) AS eauth
                FROM run_accounts GROUP BY account
            ) ra ON ra.account = a.name
            LEFT JOIN (
                SELECT account,
                       count(DISTINCT profile_id)
                           FILTER (WHERE status IN ('ok', 'empty')) AS profiles,
                       sum(posts_new) AS posts, sum(comments_new) AS comments,
                       sum(media_new_files) AS media_files
                FROM profile_runs GROUP BY account
            ) pr ON pr.account = a.name
            ORDER BY a.name
        """,
        note=(
            "runs: запуски (профили и поиск); profiles: уникальные обработанные профили; "
            "posts/comments: новые посты и комментарии; media: скачанные файлы; "
            "429 и auth: ответы 429 и проблемы авторизации"
        ),
    )
)


# Поиск, отчёт 1: общая картина — approved/rejected, авторы, средние лайки.
register(
    Report(
        name="search_overview",
        title="Поиск: общая картина",
        sql="""
            SELECT filter_status,
                   count(*)                          AS posts,
                   count(DISTINCT author_username)    AS authors,
                   round(avg(likes))::int             AS avg_likes
            FROM posts
            WHERE first_seen_via = 'search'
            GROUP BY filter_status
            ORDER BY filter_status
        """,
        note="posts: посты из поиска; authors: уникальные авторы; avg_likes: средние лайки",
    )
)

# Поиск, отчёт 2: качество каждого запроса — доля approved среди найденных им постов.
register(
    Report(
        name="search_queries",
        title="Поиск: качество запросов",
        sql="""
            SELECT sj.query,
                   sj.mode,
                   count(DISTINCT ph.post_code)                                       AS posts,
                   count(DISTINCT ph.post_code)
                       FILTER (WHERE p.filter_status = 'approved')                    AS approved,
                   round(
                       100.0 * count(DISTINCT ph.post_code)
                           FILTER (WHERE p.filter_status = 'approved')
                       / nullif(count(DISTINCT ph.post_code), 0), 1)                  AS approved_pct
            FROM search_jobs sj
            JOIN post_hits ph ON ph.job_id = sj.id
            JOIN posts p ON p.code = ph.post_code
            GROUP BY sj.query, sj.mode
            ORDER BY approved_pct DESC NULLS LAST, posts DESC
        """,
        note="posts: найдено запросом; approved: из них прошли фильтр; approved_pct: доля, %",
    )
)

# Поиск, отчёт 3: темы approved-постов.
register(
    Report(
        name="search_topics",
        title="Поиск: темы approved-постов",
        sql="""
            SELECT t.name                    AS topic,
                   count(*)                   AS posts,
                   round(avg(p.likes))::int    AS avg_likes
            FROM posts p
            JOIN topics t ON t.id = p.topic_id
            WHERE p.first_seen_via = 'search' AND p.filter_status = 'approved'
            GROUP BY t.name
            ORDER BY posts DESC
        """,
        note="posts: approved-постов с этой темой; avg_likes: средние лайки",
    )
)

# Поиск, отчёт 4: состояние очереди search_jobs.
register(
    Report(
        name="search_queue",
        title="Поиск: очередь задач",
        sql="""
            SELECT status,
                   count(*)                     AS jobs,
                   coalesce(sum(posts_found), 0) AS posts_found,
                   coalesce(sum(posts_new), 0)   AS posts_new,
                   coalesce(sum(posts_dupes), 0) AS posts_dupes,
                   round(avg(retries), 1)        AS avg_retries
            FROM search_jobs
            GROUP BY status
            ORDER BY status
        """,
        note="jobs: задач в статусе; posts_found/new/dupes: суммы по задачам; avg_retries: среднее число повторов",
    )
)


async def run_report(conn: asyncpg.Connection, report: Report) -> tuple[list[str], list[tuple]]:
    """(названия колонок, строки); заголовки есть и у пустого результата."""
    stmt = await conn.prepare(report.sql)
    rows = await stmt.fetch()
    return [a.name for a in stmt.get_attributes()], [tuple(r.values()) for r in rows]


def format_table(cols: list[str], rows: list[tuple]) -> str:
    """Таблица с выравниванием по левому краю; None показывается как «-»."""
    cells = [[("-" if v is None else str(v)) for v in row] for row in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(cols, *cells)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*cols), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*row) for row in cells]
    return "\n".join(lines)
