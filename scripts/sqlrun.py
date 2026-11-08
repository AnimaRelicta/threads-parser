#!/usr/bin/env python3
"""Точка входа: свои запросы на изменение БД из файла (аналог export.py, но с записью).

Запросы лежат в data/db_tasks.sql (путь задаёт DB_TASKS_FILE), формат как у export.py:

    -- name: select-targets
    -- desc: что делает (видно в `sqlrun.py --list`)
    -- param: min_followers=100        (значение по умолчанию, меняется через --set)
    UPDATE profiles SET is_target = (followers_count >= :min_followers::int) WHERE ...;

По умолчанию запрос выполняется в транзакции и откатывается (пробный прогон): видно, сколько
строк изменится. Записать в БД: `--apply`. UPDATE и DELETE без WHERE отклоняются.

Примеры (из корня проекта):
    uv run scripts/sqlrun.py --list
    uv run scripts/sqlrun.py select-targets                          # пробный прогон
    uv run scripts/sqlrun.py select-targets --set min_followers=200 --apply
    uv run scripts/sqlrun.py targets-stats                           # SELECT: покажет строки
"""

import argparse
import re
from pathlib import Path

import asyncpg

from config import ENV_FILE, load_settings
from db.pool import run_sync
from exporter.sqlfile import Query, load_queries_file

SHOW_ROWS = 20  # сколько строк SELECT печатать
_PARAM_LINE = re.compile(r"^--\s*param:\s*(\w+)\s*=\s*(.*?)\s*$", re.I | re.M)
_PLACEHOLDER = re.compile(r"(?<!:):([a-z_]\w*)\b", re.I)  # `::int` не затрагивается
_WRITE_NO_WHERE = re.compile(r"^\s*(UPDATE|DELETE)\b", re.I)


def coerce(value: str) -> int | float | str:
    """Значение параметра: целое, дробное или строка."""
    for kind in (int, float):
        try:
            return kind(value)
        except ValueError:
            pass
    return value


def declared_params(sql: str) -> dict[str, str]:
    """Параметры по умолчанию из строк `-- param: имя=значение`."""
    return {m.group(1): m.group(2) for m in _PARAM_LINE.finditer(sql)}


def strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def check_safe(q: Query) -> None:
    """UPDATE/DELETE без WHERE (в том числе внутри WITH) не пускаем: это почти всегда ошибка."""
    body = strip_comments(q.sql)
    if _WRITE_NO_WHERE.match(body) or re.search(r"\)\s*(UPDATE|DELETE)\b", body, re.I):
        if not re.search(r"\bWHERE\b", body, re.I):
            raise SystemExit(f"[{q.name}] UPDATE/DELETE без WHERE отклонён")


def bind(sql: str, values: dict) -> tuple[str, list]:
    """Заменяет :имя на $1, $2 (в порядке появления); неизвестный параметр = ошибка."""
    order: list[str] = []
    body = strip_comments(sql)

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise SystemExit(f"Нет значения параметра :{name} (задайте --set {name}=...)")
        if name not in order:
            order.append(name)
        return f"${order.index(name) + 1}"

    return _PLACEHOLDER.sub(repl, body), [coerce(str(values[n])) for n in order]


def parse_sets(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--set ожидает имя=значение, получено «{item}»")
        out[name.strip()] = value.strip()
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Свои запросы на изменение БД. Подключение к БД: {ENV_FILE}"
    )
    ap.add_argument("tasks", nargs="*", help="имена запросов из файла (--list)")
    ap.add_argument("--list", action="store_true", help="показать доступные запросы")
    ap.add_argument("--file", type=Path, help="файл с запросами (по умолчанию DB_TASKS_FILE)")
    ap.add_argument(
        "--set", action="append", default=[], metavar="ИМЯ=ЗНАЧЕНИЕ", help="значение параметра"
    )
    ap.add_argument("--apply", action="store_true", help="записать изменения (без флага: откат)")
    return ap


def print_list(tasks: dict[str, Query], path: Path) -> None:
    print(f"Запросы ({path}):")
    for q in tasks.values():
        params = ", ".join(f"{k}={v}" for k, v in declared_params(q.sql).items())
        print(f"  {q.name:<22} {q.desc}" + (f"  [{params}]" if params else ""))
    if not tasks:
        print("  (нет)")


async def run_task(conn: asyncpg.Connection, q: Query, values: dict, apply: bool) -> None:
    sql, args = bind(q.sql, {**declared_params(q.sql), **values})
    tx = conn.transaction()
    await tx.start()
    try:
        if re.match(r"\s*(SELECT|WITH\b(?!.*\b(UPDATE|DELETE|INSERT)\b))", sql, re.I | re.S):
            rows = await conn.fetch(sql, *args)
            print(f"[*] {q.name}: строк {len(rows)}")
            for r in rows[:SHOW_ROWS]:
                print("    " + ", ".join(f"{k}={v}" for k, v in r.items()))
            await tx.rollback()
            return
        status = await conn.execute(sql, *args)
    except asyncpg.PostgresError as e:
        await tx.rollback()
        raise SystemExit(f"[{q.name}] ошибка запроса: {type(e).__name__}: {e}") from e
    if apply:
        await tx.commit()
        print(f"[+] {q.name}: {status} (записано)")
    else:
        await tx.rollback()
        print(f"[~] {q.name}: {status} (пробный прогон, откат; записать: --apply)")


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()
    path = args.file or st.db_tasks_file
    tasks = load_queries_file(path)
    if args.list:
        print_list(tasks, path)
        return
    if not args.tasks:
        raise SystemExit("Укажите запрос: имя из --list")
    chosen = []
    for name in args.tasks:
        if name not in tasks:
            raise SystemExit(f"Нет запроса «{name}» в {path}. Список: sqlrun.py --list")
        check_safe(tasks[name])
        chosen.append(tasks[name])
    values = parse_sets(args.set)

    async def go(conn):
        for q in chosen:
            await run_task(conn, q, values, args.apply)

    run_sync(st, go)


if __name__ == "__main__":
    main()
