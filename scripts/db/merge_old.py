"""Дополнение threads.profiles данными из старой базы (другая БД, та же схема threads).

Старая БД подключается отдельным соединением (по умолчанию тот же сервер и логин, имя базы
threads_old; иначе --old-url). Профили сопоставляются по lower(username). Переносятся только
колонки, которые есть в обеих таблицах с совместимым типом (одинаковым, текст<->текст,
целое<->целое), и только в пустые места: NULL (для текста ещё и ''). Заполненное в новой БД не
перезаписывается (кроме режима overwrite). Не переносятся: ключи и ссылки на другие таблицы,
служебное состояние обработки (SKIP_COLUMNS) и колонки NOT NULL (у них своё значение по
умолчанию). Если в старой БД несколько строк одного ника, по каждой колонке берётся первое
непустое значение.
"""

import json
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from db.pool import SCHEMA, _init_conn

OLD_DB_NAME = "threads_old"
CHUNK = 2000  # профилей за один UPDATE

# Идентификаторы, внешние ключи и состояние обработки: старые значения к новым данным не подходят.
SKIP_COLUMNS = {
    "id",
    "username",
    "first_seen_at",
    "is_target",
    "last_scraped_at",
    "processed_mode",
    "discovered_via",
    "discovered_from_profile_id",
    "discovered_first_code",
    "review_status",
    "geo_version",
}

COLUMNS_SQL = """
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS full_type,
       t.typname AS udt_name, a.attnotnull AS not_null
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
WHERE n.nspname = $1 AND c.relname = 'profiles' AND a.attnum > 0 AND NOT a.attisdropped
"""

TEXT_TYPES = ("text", "varchar", "bpchar")
INT_TYPES = ("int2", "int4", "int8")


def old_url_from(database_url: str, old_url: str | None = None) -> str:
    """Адрес старой БД: --old-url или DATABASE_URL с именем базы threads_old."""
    if old_url:
        return old_url
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path=f"/{OLD_DB_NAME}"))


async def connect_old(url: str) -> asyncpg.Connection:
    try:
        conn = await asyncpg.connect(
            url, server_settings={"search_path": f"{SCHEMA},public", "application_name": "threads-merge"}
        )
    except (OSError, asyncpg.PostgresError) as e:
        raise SystemExit(f"Не удалось подключиться к старой БД: {type(e).__name__}: {e}") from e
    await _init_conn(conn)
    return conn


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _castable(a: str, b: str) -> bool:
    """Тип отличается, но значения можно привести (текст <-> текст, целое <-> целое)."""
    return (a in TEXT_TYPES and b in TEXT_TYPES) or (a in INT_TYPES and b in INT_TYPES)


async def plan_columns(
    conn: asyncpg.Connection,
    old: asyncpg.Connection,
    only: list[str] | None = None,
    exclude: list[str] | None = None,
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """([(колонка, тип в новой БД, udt)], {колонка: причина пропуска}) для общих колонок."""
    new_cols = {r["column_name"]: r for r in await conn.fetch(COLUMNS_SQL, SCHEMA)}
    old_cols = {r["column_name"]: r for r in await old.fetch(COLUMNS_SQL, SCHEMA)}
    if not old_cols:
        raise SystemExit(f"В старой БД нет таблицы {SCHEMA}.profiles")
    if "username" not in old_cols:
        raise SystemExit("В старой таблице profiles нет колонки username: не по чему сопоставлять")
    skip = SKIP_COLUMNS | set(exclude or [])
    cols: list[tuple[str, str, str]] = []
    skipped: dict[str, str] = {}
    for name, n in new_cols.items():
        if name not in old_cols:
            continue
        o = old_cols[name]
        if name in skip:
            skipped[name] = "служебная/исключена"
        elif only and name not in only:
            skipped[name] = "не в --columns"
        elif n["not_null"]:
            skipped[name] = "NOT NULL в новой БД"
        elif n["udt_name"] != o["udt_name"] and not _castable(o["udt_name"], n["udt_name"]):
            skipped[name] = f"тип {o['udt_name']} -> {n['udt_name']}"
        else:
            cols.append((name, n["full_type"], n["udt_name"]))
    return cols, skipped


def _old_select(cols: list[tuple[str, str, str]]) -> str:
    """Старая БД: одна строка на ник, по каждой колонке первое непустое значение."""
    parts = []
    for col, full, udt in cols:
        c = _q(col)
        cond = f"{c} IS NOT NULL" + (f" AND {c}::text <> ''" if udt in TEXT_TYPES else "")
        cast = f"::{full}" if udt in TEXT_TYPES + INT_TYPES else ""  # типы могут отличаться
        parts.append(f"(array_agg({c}{cast}) FILTER (WHERE {cond}))[1] AS {c}")
    return (
        f"SELECT lower(username) AS u, {', '.join(parts)} "
        f"FROM {SCHEMA}.profiles GROUP BY lower(username)"
    )


async def merge_old_profiles(
    conn: asyncpg.Connection,
    old: asyncpg.Connection,
    only: list[str] | None = None,
    exclude: list[str] | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """Дополняет threads.profiles данными старой БД. Возвращает счётчики.

    {"matched": профилей есть в обеих БД, "only_old": есть только в старой, "updated": строк
    изменено, "filled": {колонка: сколько значений внесено}, "skipped": {колонка: причина}}
    """
    cols, skipped = await plan_columns(conn, old, only, exclude)
    res: dict = {"matched": 0, "only_old": 0, "updated": 0, "filled": {}, "skipped": skipped}

    new_names = {r["u"] for r in await conn.fetch(f"SELECT lower(username) AS u FROM {SCHEMA}.profiles")}
    old_rows = [dict(r) for r in await old.fetch(_old_select(cols))] if cols else []
    if not cols:
        old_rows = [dict(r) for r in await old.fetch(f"SELECT DISTINCT lower(username) AS u FROM {SCHEMA}.profiles")]
    old_names = {r["u"] for r in old_rows}
    res["matched"] = len(new_names & old_names)
    res["only_old"] = len(old_names - new_names)
    if not cols:
        return res

    # Только ники, которые есть в обеих БД и у которых есть хоть одно значение
    rows = [
        r for r in old_rows if r["u"] in new_names and any(r[c] is not None for c, _, _ in cols)
    ]

    def needs(col: str, udt: str) -> str:
        c = f"p.{_q(col)}"
        take = f"o.{_q(col)} IS NOT NULL"
        if overwrite:
            return f"({take} AND {c} IS DISTINCT FROM o.{_q(col)})"
        empty = f"({c} IS NULL OR {c}::text = '')" if udt in TEXT_TYPES else f"{c} IS NULL"
        return f"({take} AND {empty})"

    # Старые значения приходят одним jsonb-параметром и разворачиваются в набор строк o
    recordset = (
        "jsonb_to_recordset($1::text::jsonb) AS o(u text, "
        + ", ".join(f"{_q(c)} {full}" for c, full, _ in cols)
        + ")"
    )
    count_sql = (
        "SELECT "
        + ", ".join(f"count(*) FILTER (WHERE {needs(c, u)}) AS {_q(c)}" for c, _, u in cols)
        + f" FROM {SCHEMA}.profiles p JOIN {recordset} ON o.u = lower(p.username)"
    )
    filled = dict.fromkeys((c for c, _, _ in cols), 0)
    for i in range(0, len(rows), CHUNK):
        payload = json.dumps(rows[i : i + CHUNK], ensure_ascii=False, default=str)
        counts = await conn.fetchrow(count_sql, payload)
        for c in filled:
            filled[c] += counts[c]
    res["filled"] = {c: n for c, n in filled.items() if n}
    if dry_run or not res["filled"]:
        return res

    todo = [(c, full, u) for c, full, u in cols if c in res["filled"]]
    sets = ", ".join(
        f"{_q(c)} = CASE WHEN {needs(c, u)} THEN o.{_q(c)} ELSE p.{_q(c)} END" for c, _, u in todo
    )
    where = " OR ".join(needs(c, u) for c, _, u in todo)
    update_sql = (
        f"UPDATE {SCHEMA}.profiles p SET {sets} FROM {recordset} "
        f"WHERE o.u = lower(p.username) AND ({where})"
    )
    async with conn.transaction():
        for i in range(0, len(rows), CHUNK):
            payload = json.dumps(rows[i : i + CHUNK], ensure_ascii=False, default=str)
            out = await conn.execute(update_sql, payload)
            res["updated"] += int(out.split()[-1])
    return res
