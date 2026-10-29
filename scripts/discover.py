#!/usr/bin/env python3
"""Точка входа: новые профили по авторам постов под ветками и авторам веток из поиска.

Работает только с БД, без браузера. Ники, которых нет в profiles, заводятся профилями
(is_target = true, review_status = 'pending_review'); повторный запуск ничего не добавляет.

Примеры (из корня проекта):
    uv run scripts/discover.py --since 7d --dry-run   # только показать кандидатов
    uv run scripts/discover.py --since 7d             # завести профили
    uv run scripts/discover.py --since 1m --min-mentions 3
    uv run scripts/run.py --source authors --threads-only --since 7d   # собрать их ветки
"""

import argparse

from config import ENV_FILE, load_settings
from core.timeutil import parse_since
from db.discovery import Candidate, discover_authors
from db.pool import run_sync

VIA_TITLE = {"author": "авторы постов под ветками", "search": "авторы веток из поиска"}


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Поиск новых профилей по авторам. Настройки по умолчанию: {ENV_FILE}"
    )
    ap.add_argument(
        "--since",
        default=st.since,
        help="учитывать посты и ветки не старше периода: 30d, 4w, 1m, 1y или дата "
        "2026-09-01; без значения весь накопленный период (SINCE)",
    )
    ap.add_argument(
        "--min-mentions",
        type=int,
        default=1,
        help="брать авторов, встретившихся не меньше N раз (отсекает случайных, по умолчанию 1)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="ничего не записывать, только показать, кого добавил бы запуск",
    )
    ap.add_argument(
        "--show",
        type=int,
        default=15,
        help="сколько кандидатов вывести в список, 0 = все (по умолчанию 15)",
    )
    return ap


def print_candidates(candidates: list[Candidate], show: int) -> None:
    shown = candidates if show <= 0 else candidates[:show]
    for c in shown:
        print(f"  @{c.username:<30} {c.via:<7} встреч: {c.mentions:<4} ветка: {c.first_code}")
    if len(shown) < len(candidates):
        print(f"  ... и ещё {len(candidates) - len(shown)}")


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()
    if args.min_mentions < 1:
        raise SystemExit("--min-mentions должен быть не меньше 1")
    since = parse_since(args.since)
    res = run_sync(st, lambda conn: discover_authors(conn, since, args.min_mentions, args.dry_run))
    period = f"с {since:%Y-%m-%d %H:%M} UTC" if since else "за всё время"
    total = len(res.candidates)
    if not total:
        print(f"[*] Новых авторов нет ({period}, встреч не меньше {args.min_mentions})")
        return
    by_via = {v: sum(1 for c in res.candidates if c.via == v) for v in VIA_TITLE}
    print(f"[*] Кандидатов {period}: {total}")
    for via, title in VIA_TITLE.items():
        print(f"    {title}: {by_via[via]}")
    print_candidates(res.candidates, args.show)
    if args.dry_run:
        print("[*] --dry-run: профили не добавлены")
        return
    added = sum(res.added.values())
    print(f"[+] Добавлено профилей: {added} (pending_review)")
    if added < total:
        print(f"[*] Уже были заведены параллельным запуском: {total - added}")


if __name__ == "__main__":
    main()
