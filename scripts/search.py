#!/usr/bin/env python3
"""Точка входа: сбор результатов поиска Threads (посты по запросу).

Примеры (из корня проекта):
    uv run scripts/search.py "олх ветка астана" --recon
    uv run scripts/search.py --file data/threads_queries.txt --since 7d
    uv run scripts/search.py "олх ветка" --after 2026-10-20 --before 2026-10-27 \\
        --window-days 3 --modes recent top
    uv run scripts/search.py --refilter   # пересчитать approved/rejected в БД
"""

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from config import ENV_FILE, load_settings
from core.timeutil import parse_since
from db.pool import run_sync
from db.search import refilter
from db.stats import REPORTS, format_table, run_report
from serp.recon import run_recon
from serp.runner import run_search
from serp.tasks import MODES, build_tasks, load_queries, parse_date

# Отчёты поиска, в порядке вывода --stats (сами отчёты — в db/stats.py).
STATS_REPORTS = ["search_overview", "search_queries", "search_queue"]


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Поиск Threads через Playwright. Настройки по умолчанию: {ENV_FILE}"
    )
    ap.add_argument("query", nargs="*", help="поисковый запрос (можно несколько слов)")
    ap.add_argument(
        "--file",
        help="файл с запросами, по одному на строку "
        "(по умолчанию SEARCH_QUERIES_FILE, если запрос не указан)",
    )
    ap.add_argument(
        "--modes",
        nargs="+",
        choices=MODES,
        default=list(MODES),
        help="режимы поиска (по умолчанию top и recent)",
    )
    ap.add_argument(
        "--accounts",
        type=lambda v: [a.strip() for a in v.split(",") if a.strip()],
        default=st.accounts,
        help="аккаунты через запятую (по умолчанию все активные)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=st.search_workers,
        help="воркеров на аккаунт, m: вкладок в одном браузере аккаунта "
        "(SEARCH_WORKERS); задачи берутся из общей очереди на всех "
        "аккаунтов и их воркеров",
    )
    ap.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=st.headless,
        help="без окна браузера (HEADLESS)",
    )
    ap.add_argument("--after", help="дата начала окна, YYYY-MM-DD")
    ap.add_argument("--before", help="дата конца окна, YYYY-MM-DD")
    ap.add_argument(
        "--since",
        help="период до сегодня: 7d, 1m и т.п. (как в run.py); перекрывается --after/--before",
    )
    ap.add_argument(
        "--window-days",
        type=int,
        default=st.search_window_days,
        help="резать период на окна по N дней, 0 = одно окно (SEARCH_WINDOW_DAYS)",
    )
    ap.add_argument(
        "--max-posts", type=int, default=0, help="лимит постов на задачу, 0 = без лимита"
    )
    ap.add_argument(
        "--max-scrolls", type=int, default=0, help="лимит прокруток на задачу, 0 = без лимита"
    )
    ap.add_argument(
        "--recon",
        action="store_true",
        help="режим разведки: один запрос, сырые JSON в output/_raw/, поиск темы",
    )
    ap.add_argument(
        "--needle", help="что искать в JSON на --recon (по умолчанию первые 1-2 слова запроса)"
    )
    ap.add_argument("--force", action="store_true", help="перезапустить уже готовые окна")
    ap.add_argument(
        "--output",
        default=str(st.search_output_dir),
        help="папка сырых данных для --recon (SEARCH_OUTPUT_DIR); результаты поиска пишутся в БД",
    )
    ap.add_argument(
        "--refilter",
        action="store_true",
        help="только пересчитать approved/rejected в БД по текущим правилам фильтра "
        "(посты со старой версией правил; с --force все посты), без браузера",
    )
    ap.add_argument(
        "--stats",
        action="store_true",
        help="только показать статистику поиска из БД (общая картина, качество запросов, "
        "темы, очередь задач), без браузера",
    )
    return ap


def resolve_window(args):
    """--after/--before важнее --since (как SINCE/LIMIT в run.py)."""
    after = parse_date(args.after)
    before = parse_date(args.before)
    if args.since and not after and not before:
        since_dt = parse_since(args.since)
        if since_dt is not None:
            after = since_dt.date()
            before = datetime.now(timezone.utc).date()
    return after, before


def refilter_stored_posts(st, args) -> None:
    """Отдельный режим: пересчёт filter_status у уже сохранённых постов."""
    res = run_sync(st, lambda conn: refilter(conn, everything=args.force))
    print(f"[*] Проверено постов: {res['checked']}, статус изменился: {res['changed']}")


def print_stats(st) -> None:
    """Отдельный режим: таблицы отчётов поиска из БД (см. STATS_REPORTS, db/stats.py)."""

    async def _all(conn):
        return [(name, *await run_report(conn, REPORTS[name])) for name in STATS_REPORTS]

    for name, cols, rows in run_sync(st, _all):
        report = REPORTS[name]
        print(f"\n### {report.title}")
        if not rows:
            print("(нет данных)")
            continue
        print(format_table(cols, rows))
        if report.note:
            print(report.note)


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()

    # Пересчёт фильтра и статистика: без браузера, аккаунтов и запросов
    if args.refilter:
        refilter_stored_posts(st, args)
        return
    if args.stats:
        print_stats(st)
        return

    queries: list[str] = []
    if args.query:
        queries.append(" ".join(args.query))
    if args.file or (not queries and not args.recon):
        queries.extend(load_queries(Path(args.file) if args.file else st.search_queries_file))
    if not queries:
        raise SystemExit("Укажите запрос или заполните файл со списком запросов (--file)")
    if args.recon and len(queries) > 1:
        queries = queries[:1]
        print(f"[*] --recon: беру только первый запрос «{queries[0]}»")

    after, before = resolve_window(args)
    modes = args.modes
    if args.recon and len(modes) != 1:
        modes = ["recent"]  # recon проверяет один режим за раз
    tasks = build_tasks(queries, modes, after, before, args.window_days)

    print(
        f"[*] Запросов: {len(queries)} | режимов: {len(modes)} | задач: {len(tasks)} "
        f"| воркеров: {args.workers} | headless: {args.headless}"
    )
    if after or before:
        print(
            f"[*] Окно: {after or '..'} .. {before or '..'}"
            + (f", нарезка по {args.window_days} дн." if args.window_days > 0 else "")
        )
    for t in tasks[:10]:
        print(f"    {t.label()} -> {t.url()}")
    if len(tasks) > 10:
        print(f"    ... и ещё {len(tasks) - 10}")

    if args.recon:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        asyncio.run(run_recon(st, args, tasks[0]))
        return

    run_search(st, args, tasks)


if __name__ == "__main__":
    main()
