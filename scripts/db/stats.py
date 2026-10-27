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
                       count(DISTINCT profile_id) FILTER (WHERE status IN ('ok', 'empty')) AS profiles,
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
