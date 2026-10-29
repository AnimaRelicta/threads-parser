"""Статистика из БД: реестр отчётов (SQL + заголовок) и вывод таблицей.

Новый отчёт = одна запись в REPORTS; команды (accounts.py list, search.py --stats и др.)
берут отчёты отсюда по имени и ничего не знают про SQL.
"""

from dataclasses import dataclass

import asyncpg

from db.pool import run_sync


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


# Профили, отчёт 1: очередь на проверку (review_status) по происхождению профиля.
register(
    Report(
        name="profiles_review_queue",
        title="Профили: очередь на проверку",
        sql="""
            SELECT p.discovered_via,
                   p.review_status,
                   count(*) AS profiles,
                   count(*) FILTER (
                       WHERE NOT EXISTS (SELECT 1 FROM posts t WHERE t.profile_id = p.id)
                   ) AS without_threads
            FROM profiles p
            GROUP BY p.discovered_via, p.review_status
            ORDER BY p.discovered_via, p.review_status
        """,
        note="without_threads: профилей, у которых ещё нет ни одной собранной ветки",
    )
)

# Профили, отчёт 2: ветки быстрого режима (--threads-only) и сколько ещё без разбора.
register(
    Report(
        name="threads_quick",
        title="Ветки: быстрый сбор и разбор",
        sql="""
            SELECT
                (SELECT coalesce(sum(posts_new), 0) FROM profile_runs
                 WHERE mode = 'threads') AS collected_quick,
                (SELECT count(*) FROM posts
                 WHERE first_seen_via = 'profile' AND last_refreshed_at IS NULL) AS unparsed,
                (SELECT count(DISTINCT profile_id) FROM posts
                 WHERE first_seen_via = 'profile' AND last_refreshed_at IS NULL) AS profiles_waiting
        """,
        note="collected_quick: новых веток за все запуски --threads-only; unparsed: веток профилей "
        "без разбора (без постов под веткой, ждут --fill-threads); profiles_waiting: таких профилей",
    )
)

# Профили, отчёт 3: последние запуски discover (итоги лежат в runs.settings).
register(
    Report(
        name="discover_runs",
        title="Поиск новых профилей: последние запуски",
        sql="""
            SELECT id,
                   to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS started,
                   status,
                   settings->>'since' AS since,
                   settings->>'found' AS found,
                   settings->>'added' AS added,
                   left(coalesce(error, ''), 40) AS error
            FROM runs
            WHERE kind = 'discover'
            ORDER BY id DESC
            LIMIT 10
        """,
        note="found: кандидатов, added: заведено профилей; пробные запуски --dry-run не пишутся",
    )
)


# Классификация, отчёт 1: распределение профилей по категориям.
register(
    Report(
        name="profiles_by_category",
        title="Профили по категориям",
        sql="""
            SELECT coalesce(c.name_ru, '(без категории)') AS category,
                   count(*) AS profiles,
                   round(avg((p.category_raw->>'confidence')::numeric), 2) AS avg_confidence,
                   count(*) FILTER (WHERE p.review_status = 'review_failed') AS failed
            FROM profiles p
            LEFT JOIN categories c ON c.id = p.category_id
            WHERE p.review_status <> 'new' OR p.category_id IS NOT NULL
            GROUP BY c.id, c.name_ru
            ORDER BY count(*) DESC
        """,
        note="avg_confidence: средняя уверенность модели; failed: профилей в review_failed",
    )
)

# Классификация, отчёт 2: среднее время классификации профиля по моделям.
register(
    Report(
        name="classify_latency",
        title="Классификация: время на профиль",
        sql="""
            SELECT coalesce(category_model, '-') AS model,
                   count(*) AS profiles,
                   round(avg((category_raw->>'latency_ms')::numeric)) AS avg_ms,
                   round(avg((category_raw->>'requests')::numeric), 2) AS avg_requests
            FROM profiles
            WHERE category_raw->>'latency_ms' IS NOT NULL
            GROUP BY category_model
            ORDER BY count(*) DESC
        """,
        note="avg_ms: от первого запроса до ответа с учётом повторов; по последней попытке профиля",
    )
)

# Классификация, отчёт 3: расход запросов к LLM по дням (итоги лежат в runs.settings).
register(
    Report(
        name="classify_usage",
        title="Классификация: расход запросов к LLM",
        sql="""
            SELECT to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
                   count(*) AS runs,
                   sum(coalesce((settings->>'processed')::int, 0)) AS profiles,
                   sum(coalesce((settings->>'requests')::int, 0)) AS requests,
                   sum(coalesce((settings->>'prompt_tokens')::int, 0)) AS prompt_tokens,
                   sum(coalesce((settings->>'completion_tokens')::int, 0)) AS completion_tokens
            FROM runs
            WHERE kind = 'classify'
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 14
        """,
        note="requests: HTTP-запросов к proxy API с повторами; токены, если proxy их возвращает",
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


def print_reports(st, names: list[str]) -> None:
    """Печатает таблицы отчётов из БД по именам (для команд --stats)."""

    async def _all(conn):
        return [(name, *await run_report(conn, REPORTS[name])) for name in names]

    for name, cols, rows in run_sync(st, _all):
        report = REPORTS[name]
        print(f"\n### {report.title}")
        if not rows:
            print("(нет данных)")
            continue
        print(format_table(cols, rows))
        if report.note:
            print(report.note)
