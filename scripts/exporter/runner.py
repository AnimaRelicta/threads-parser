"""Общий движок выгрузки в xlsx: им пользуются `export.py` (схема threads) и `cli.py ig export`.

Схема, базовые запросы, файл своих запросов и папка выгрузки задаются `ExportSpec`; сам разбор
флагов, read-only транзакция и запись xlsx одинаковы для всех схем.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg

from config import ENV_FILE
from core.timeutil import parse_since
from db.pool import run_sync
from exporter.sqlfile import Query, bind, load_queries_file, used_params
from exporter.xlsx import write_workbook


@dataclass(frozen=True)
class ExportSpec:
    prog: str  # как команда называется в сообщениях: «export.py», «cli.py ig export»
    schema: str  # рабочая схема БД (search_path)
    base: dict[str, Query]  # базовые запросы схемы
    queries_file: Path  # свои запросы по умолчанию
    export_dir: Path  # куда писать xlsx по умолчанию


def build_parser(spec: ExportSpec) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=spec.prog,
        description=f"Выгрузка из Postgres (схема {spec.schema}) в xlsx. Подключение к БД: {ENV_FILE}",
    )
    ap.add_argument("queries", nargs="*", help="имена запросов: базовые или из файла (--list)")
    ap.add_argument("--list", action="store_true", help="показать доступные запросы")
    ap.add_argument("--sql", metavar="ЗАПРОС", help="разовый запрос текстом")
    ap.add_argument("--file", type=Path, metavar="ФАЙЛ.sql", help="разовый запрос из файла")
    ap.add_argument("--sheet", help="имя листа для --sql / --file (по умолчанию custom)")
    ap.add_argument(
        "--queries-file",
        type=Path,
        help=f"файл со своими запросами (по умолчанию {spec.queries_file})",
    )
    ap.add_argument(
        "--out",
        help="куда писать .xlsx; папка = положить в неё. По умолчанию папка выгрузки/имя_дата.xlsx",
    )
    ap.add_argument("--since", help=":since в запросе: 30d, 4w, 1m, 1y или дата 2026-09-01")
    ap.add_argument("--limit", type=int, default=0, help=":limit в запросе, 0 = все строки")
    return ap


def available(spec: ExportSpec, path: Path) -> dict[str, Query]:
    """Базовые запросы + свои из файла; при совпадении имён побеждает файл."""
    return {**spec.base, **load_queries_file(path)}


def print_list(spec: ExportSpec, path: Path) -> None:
    allq = available(spec, path)
    for src, title in (("base", "Базовые"), ("file", f"Свои ({path})")):
        group = [q for q in allq.values() if q.source == src]
        print(f"{title}:")
        for q in group or []:
            print(f"  {q.name:<16} {q.desc}")
        if not group:
            print("  (нет)")
    print("\nПараметры в запросах: :since (--since) и :limit (--limit)")


def collect(spec: ExportSpec, args, path: Path) -> list[Query]:
    allq = available(spec, path)
    out: list[Query] = []
    for name in args.queries:
        if name not in allq:
            raise SystemExit(f"Нет запроса «{name}». Список: {spec.prog} --list")
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


def export_main(spec: ExportSpec, st, argv: list[str] | None = None) -> None:
    """st: Settings или IgSettings (нужен только database_url)."""
    args = build_parser(spec).parse_args(argv)
    qfile = args.queries_file or spec.queries_file
    if args.list:
        print_list(spec, qfile)
        return
    queries = collect(spec, args, qfile)
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

    sheets = run_sync(st, go, spec.schema)
    path = out_path(args, spec.export_dir, queries)
    for w in write_workbook(path, sheets):
        print(f"[!] {w}")
    print(f"[+] Записано: {path} (листов {len(sheets)}, даты в UTC)")
