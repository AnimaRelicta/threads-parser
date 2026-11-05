#!/usr/bin/env python3
"""Точка входа: сбор веток по списку ссылок (data/threads_posts.txt) в таблицу posts.

Примеры (из корня проекта):
    uv run scripts/postlist.py                           # ветки из POSTS_FILE
    uv run scripts/postlist.py --file data/threads_posts.txt
    uv run scripts/postlist.py https://www.threads.com/@ник/post/КОД
    uv run scripts/postlist.py --refresh                 # ещё и обновить уже сохранённые
    uv run scripts/postlist.py --refresh --stale-days 3  # обновить только не менявшиеся 3+ дня
    uv run scripts/postlist.py --dry-run                 # только показать, что будет собрано
"""

import argparse
from pathlib import Path

from config import ENV_FILE, load_settings
from serp.postlist import load_entries, merge_entries, parse_entries, plan_sync, run_postlist


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Сбор веток по списку ссылок в posts. Настройки по умолчанию: {ENV_FILE}"
    )
    ap.add_argument("links", nargs="*", help="ссылки на ветки (можно вместе с --file)")
    ap.add_argument(
        "--file",
        help="файл со ссылками, по одной на строку "
        "(по умолчанию POSTS_FILE, если ссылки не указаны)",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="открыть заново и обновить ветки, которые уже есть в БД "
        "(без флага они пропускаются, собираются только новые)",
    )
    ap.add_argument(
        "--stale-days",
        type=int,
        default=0,
        help="с --refresh: обновлять только ветки, не менявшиеся столько дней (0 = все)",
    )
    ap.add_argument(
        "--accounts",
        type=lambda v: [a.strip() for a in v.split(",") if a.strip()],
        default=st.accounts,
        help="аккаунты через запятую (по умолчанию все активные)",
    )
    ap.add_argument(
        "--workers", type=int, default=st.posts_workers, help="вкладок на аккаунт (POSTS_WORKERS)"
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=st.posts_delay,
        help="пауза между ветками на вкладке, сек; реальная 0.5x..1.5x (POSTS_DELAY)",
    )
    ap.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=st.headless,
        help="без окна браузера (HEADLESS)",
    )
    ap.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=st.debug,
        help="подробный лог (DEBUG)",
    )
    ap.add_argument(
        "--no-block",
        action="store_true",
        help="не блокировать загрузку картинок и шрифтов на вкладках",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="только разобрать файл и сверить с БД: сколько новых и к обновлению, без браузера",
    )
    return ap


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()

    from_args = parse_entries(args.links)
    from_file = load_entries(Path(args.file) if args.file else st.posts_file) if (
        args.file or not from_args
    ) else []
    entries = merge_entries(from_args, from_file)
    if not entries:
        raise SystemExit("Нет веток: укажите ссылки или заполните data/threads_posts.txt")

    tasks, plan = plan_sync(st, entries, args.refresh, args.stale_days)
    print(
        f"[*] В списке: {plan['total']} | новых: {plan['new']} | "
        f"уже в БД (пропущено): {plan['in_db']} | к обновлению (--refresh): {plan['refresh']} | "
        f"свежих (--stale-days): {plan['fresh']} | без ника: {plan['no_user']}"
    )
    if not tasks:
        print("[*] Собирать нечего." + ("" if args.refresh else " Для обновления есть --refresh."))
        return
    if args.dry_run:
        for t in tasks[:10]:
            print(f"    {'обновить' if t.known else 'новая'}: @{t.username}/{t.code}")
        if len(tasks) > 10:
            print(f"    ... и ещё {len(tasks) - 10}")
        return
    run_postlist(st, args, tasks, plan)


if __name__ == "__main__":
    main()
