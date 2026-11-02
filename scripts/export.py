#!/usr/bin/env python3
"""Точка входа: выгрузка данных из Postgres в xlsx (по листу на запрос).

Запросы: базовые (`--list`), свои из файла data/export_queries.sql (EXPORT_QUERIES_FILE),
разовые через --sql и --file. Все идут в read-only транзакции: записать или удалить
запросом нельзя. В тексте запроса доступны :since и :limit (флаги --since и --limit).

Примеры (из корня проекта):
    uv run scripts/export.py --list
    uv run scripts/export.py spam-classes                           # data/export/..._время.xlsx
    uv run scripts/export.py spam-classes banned --since 7d         # два листа в одном файле
    uv run scripts/export.py top-authors --since 1m --limit 500 --out data/export/top.xlsx
    uv run scripts/export.py my-query                               # из data/export_queries.sql
    uv run scripts/export.py --sql "SELECT username FROM profiles WHERE is_banned" --sheet bans
    uv run scripts/export.py --file queries/one.sql --out report.xlsx
"""

import argparse
from datetime import datetime
from pathlib import Path

import asyncpg

from config import ENV_FILE, load_settings
from core.timeutil import parse_since
from db.pool import run_sync
from exporter.queries import BASE_QUERIES
from exporter.sqlfile import Query, bind, load_queries_file, used_params
from exporter.xlsx import write_workbook


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Выгрузка из Postgres в xlsx. Подключение к БД: {ENV_FILE}"
    )
    ap.add_argument("queries", nargs="*", help="имена запросов: базовые или из файла (--list)")
    ap.add_argument("--list", action="store_true", help="показать доступные запросы")
    ap.add_argument("--sql", metavar="ЗАПРОС", help="разовый запрос текстом")
    ap.add_argument("--file", type=Path, metavar="ФАЙЛ.sql", help="разовый запрос из файла")
    ap.add_argument("--sheet", help="имя листа для --sql / --file (по умолчанию custom)")
    ap.add_argument(
        "--queries-file",
        type=Path,
        help="файл со своими запросами (по умолчанию EXPORT_QUERIES_FILE)",
    )
    ap.add_argument(
        "--out",
        help="куда писать .xlsx; папка = положить в неё. По умолчанию EXPORT_DIR/имя_дата.xlsx",
    )
    ap.add_argument("--since", help=":since в запросе: 30d, 4w, 1m, 1y или дата 2026-09-01")
    ap.add_argument("--limit", type=int, default=0, help=":limit в запросе, 0 = все строки")
    return ap


def available(path: Path) -> dict[str, Query]:
    """Базовые запросы + свои из файла; при совпадении имён побеждает файл."""
    return {**BASE_QUERIES, **load_queries_file(path)}


def print_list(path: Path) -> None:
    allq = available(path)
    for src, title in (("base", "Базовые"), ("file", f"Свои ({path})")):
        group = [q for q in allq.values() if q.source == src]
        print(f"{title}:")
        for q in group or []:
            print(f"  {q.name:<16} {q.desc}")
        if not group:
            print("  (нет)")
    print("\nПараметры в запросах: :since (--since) и :limit (--limit)")


def collect(args, path: Path) -> list[Query]:
    allq = available(path)
    out: list[Query] = []
    for name in args.queries:
        if name not in allq:
            raise SystemExit(f"Нет запроса «{name}». Список: export.py --list")
        out.append(allq[name])
    sheet = args.sheet or "custom"
    if args.file:
        if not args.file.exists():
            raise SystemExit(f"Файл не найден: {args.file}")
        sql = args.file.read_text(encoding="utf-8-sig").strip().rstrip(";").strip()
        out.append(Query(args.sheet or args.file.stem, "", sql, "cli"))
    if args.sql:
        out.append(Query(sheet, "", args.sql.strip().rstrip(";").strip(), "cli"))
    return out


def out_path(args, export_dir: Path, queries: list[Query]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    base = "+".join(q.name for q in queries)[:60]
    default = f"{base}_{stamp}.xlsx"
    if not args.out:
        return export_dir / default
    out = Path(args.out)
    if out.is_dir() or args.out.endswith(("/", "\\")):  # Path съедает хвостовой слэш
        return out / default
    return out if out.suffix.lower() == ".xlsx" else out.with_suffix(".xlsx")


async def run_query(conn, q: Query, values: dict) -> tuple[list[str], list[tuple]]:
    sql, params = bind(q.sql, values)
    try:
        # read-only: INSERT/UPDATE/DELETE/DDL в запросе завершатся ошибкой Postgres
        async with conn.transaction(readonly=True):
            stmt = await conn.prepare(sql)
            cols = [a.name for a in stmt.get_attributes()]
            if not cols:
                raise SystemExit(f"[{q.name}] запрос не возвращает строк (нужен SELECT)")
            rows = [tuple(r.values()) for r in await stmt.fetch(*params)]
    except asyncpg.PostgresError as e:
        raise SystemExit(f"[{q.name}] ошибка запроса: {type(e).__name__}: {e}") from e
    return cols, rows


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()
    qfile = args.queries_file or st.export_queries_file
    if args.list:
        print_list(qfile)
        return
    queries = collect(args, qfile)
    if not queries:
        raise SystemExit("Укажите запрос: имя из --list, --sql или --file")
    if args.limit < 0:
        raise SystemExit("--limit не может быть отрицательным")
    values = {"since": parse_since(args.since), "limit": args.limit or None}
    for q in queries:
        for flag, name in (("since", "--since"), ("limit", "--limit")):
            if values[flag] is not None and flag not in used_params(q.sql):
                print(f"[!] [{q.name}] запрос не использует :{flag}, {name} проигнорирован")

    async def go(conn):
        sheets = []
        for q in queries:
            cols, rows = await run_query(conn, q, values)
            print(f"[*] {q.name}: строк {len(rows)}")
            sheets.append((q.name, cols, rows))
        return sheets

    sheets = run_sync(st, go)
    path = out_path(args, st.export_dir, queries)
    for w in write_workbook(path, sheets):
        print(f"[!] {w}")
    print(f"[+] Записано: {path} (листов {len(sheets)}, даты в UTC)")


if __name__ == "__main__":
    main()
