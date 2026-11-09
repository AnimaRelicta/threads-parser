#!/usr/bin/env python3
"""Точка входа: сводный отчёт из Postgres в один xlsx (по листу на блок data/build_report.sql).

Что войдёт в отчёт, листы, параметры и запросы описаны в самом файле `data/build_report.sql`
(REPORT_FILE или --file); здесь только управление флагами. Все запросы идут в read-only
транзакции. Имя файла: threads_report_ГГГГ-ММ-ДД.xlsx в REPORT_DIR.

Примеры (из корня проекта):
    uv run scripts/build_report.py --list
    uv run scripts/build_report.py                                  # листы с enabled: on
    uv run scripts/build_report.py --since 1m                       # только период: 30d, 4w, 1m, 1y, дата
    uv run scripts/build_report.py --only summary categories        # только эти листы
    uv run scripts/build_report.py --on comments --off instagram    # добавить / убрать лист
    uv run scripts/build_report.py --set home_country=UZ            # параметр во всех листах
    uv run scripts/build_report.py --set posts-kz.limit=1000        # параметр одного листа
    uv run scripts/build_report.py --out data/report/                # куда писать
"""

import argparse
from pathlib import Path

from config import ENV_FILE, load_settings
from db.pool import run_sync
from exporter.xlsx import write_workbook
from reporter.runner import check_sets, parse_sets, run_block
from reporter.selection import out_path, select_blocks
from reporter.spec import check_params, load_report


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Сводный отчёт из Postgres в xlsx. Подключение к БД: {ENV_FILE}"
    )
    ap.add_argument("--list", action="store_true", help="показать листы, параметры и их статус")
    ap.add_argument("--file", type=Path, metavar="ФАЙЛ.sql", help="описание отчёта (REPORT_FILE)")
    ap.add_argument("--only", nargs="+", default=[], metavar="ЛИСТ", help="только эти листы")
    ap.add_argument("--on", nargs="+", default=[], metavar="ЛИСТ", help="включить лист")
    ap.add_argument("--off", nargs="+", default=[], metavar="ЛИСТ", help="выключить лист")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="ИМЯ=ЗНАЧЕНИЕ",
        help="параметр запроса: имя=значение (всем листам) или лист.имя=значение; можно несколько",
    )
    ap.add_argument("--since", help="то же, что --set since=...: 30d, 4w, 1m, 1y или 2026-09-01")
    ap.add_argument(
        "--out", help="файл .xlsx или папка. По умолчанию REPORT_DIR/threads_report_ДАТА.xlsx"
    )
    return ap


def print_list(blocks, path: Path) -> None:
    print(f"Отчёт: {path}")
    for b in blocks:
        mark = "on " if b.enabled else "off"
        sheet = f" -> «{b.sheet}»" if b.sheet != b.name else ""
        print(f"  [{mark}] {b.name:<18}{sheet}  {b.desc}")
        for k, v in b.params.items():
            print(f"           :{k} = {v or '(пусто = NULL)'}")


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()
    path = args.file or st.report_file
    blocks = load_report(path)
    if args.list:
        print_list(blocks, path)
        return

    overrides = parse_sets(args.set)
    check_sets(blocks, overrides)
    picked = select_blocks(blocks, args.only, args.on, args.off)
    for b in picked:
        check_params(b)
    if args.since is not None:
        overrides.setdefault("since", args.since)  # --set since=... главнее
        if not any("since" in b.params for b in picked):
            print("[!] ни один выбранный лист не использует :since, --since проигнорирован")

    async def go(conn):
        sheets = []
        for b in picked:
            cols, rows, used = await run_block(conn, b, overrides)
            shown = ", ".join(f"{k}={v or 'NULL'}" for k, v in used.items())
            print(f"[*] {b.name}: строк {len(rows)}" + (f" ({shown})" if shown else ""))
            sheets.append((b.sheet, cols, rows))
        return sheets

    sheets = run_sync(st, go)
    out = out_path(args.out, st.report_dir)
    for w in write_workbook(out, sheets):
        print(f"[!] {w}")
    print(f"[+] Записано: {out} (листов {len(sheets)}, даты в UTC)")


if __name__ == "__main__":
    main()
