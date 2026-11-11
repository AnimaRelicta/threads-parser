"""Подкоманда `cli.py ig profiles`: занос профилей в instagram.profiles, список, статистика.

    uv run scripts/cli.py ig profiles add ник1 @ник2 https://instagram.com/ник3/
    uv run scripts/cli.py ig profiles add --file data/ig_profiles.txt
    uv run scripts/cli.py ig profiles add --from-threads --threads-target-only --dry-run
    uv run scripts/cli.py ig profiles list --source threads --status new --limit 20
    uv run scripts/cli.py ig profiles stats

Браузер не открывается: команда только кладёт ники в БД (парсинг — `ig parse`).
"""

import argparse
from pathlib import Path

import asyncpg

from config import IgSettings, load_ig_settings
from db.pool import run_sync
from ig.consts import IG_SCHEMA
from ig.db.profiles import ImportReport, add_candidates, list_profiles, profile_stats
from ig.db.runs import finish_run, start_run
from ig.db.sources import (
    SOURCES,
    Rejected,
    collapse,
    read_nick_file,
    threads_candidates,
    threads_query,
    to_candidates,
)

STATUSES = ("new", "ok", "private", "not_found", "error")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py ig profiles", description="Профили Instagram: занос, список, статистика"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="занести профили в БД (браузер не нужен)")
    a.add_argument("nicks", nargs="*", help="ники, @ники или ссылки; источник cli")
    a.add_argument("--file", type=Path, help="файл: ник, @ник или ссылка на строку; источник file")
    a.add_argument("--from-threads", action="store_true", help="связи из threads.instagram_links")
    a.add_argument(
        "--threads-confidence",
        choices=("confirmed", "guessed", "all"),
        default="confirmed",
        help="какие связи брать (по умолчанию confirmed: «догадки» дают много пустых ников)",
    )
    a.add_argument("--threads-target-only", action="store_true", help="только threads is_target")
    a.add_argument(
        "--threads-location", help="подстрока в threads about_location, например Казахстан"
    )
    a.add_argument("--threads-min-followers", type=int, help="не меньше N подписчиков в Threads")
    a.add_argument("--threads-category", type=int, help="id категории профиля в Threads")
    a.add_argument("--limit", type=int, default=0, help="сколько связей прочитать из threads")
    a.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не писать")

    ls = sub.add_parser("list", help="показать профили")
    ls.add_argument("--source", choices=SOURCES, help="встречался в источнике")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--limit", type=int, default=50)

    sub.add_parser("stats", help="по источникам, статусам, обработке, связи с threads")
    return ap


def check_add_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Проверка сочетаний флагов `add`."""
    if not (args.nicks or args.file or args.from_threads):
        ap.error("укажите ники, --file или --from-threads")
    threads_flags = (
        args.threads_target_only,
        args.threads_location,
        args.threads_min_followers is not None,
        args.threads_category is not None,
        args.threads_confidence != "confirmed",
        args.limit,
    )
    if any(threads_flags) and not args.from_threads:
        ap.error("--threads-* и --limit работают только вместе с --from-threads")
    if args.limit < 0:
        ap.error("--limit не может быть отрицательным")


async def gather(
    conn: asyncpg.Connection, args: argparse.Namespace, rejected: Rejected
) -> tuple[list, dict[str, int]]:
    """Кандидаты из всех указанных источников и число прочитанных записей по каждому."""
    found = []
    read: dict[str, int] = {}
    if args.nicks:
        found += to_candidates(args.nicks, "cli", None, rejected)
        read["cli"] = len(args.nicks)
    if args.file:
        raws = read_nick_file(args.file)
        found += to_candidates(raws, "file", args.file.name, rejected)
        read["file"] = len(raws)
    if args.from_threads:
        sql, params = threads_query(
            args.threads_confidence,
            args.threads_target_only,
            args.threads_location,
            args.threads_min_followers,
            args.threads_category,
            args.limit,
        )
        rows = [dict(r) for r in await conn.fetch(sql, *params)]
        found += threads_candidates(rows, rejected)
        read["threads"] = len(rows)
    return found, read


def print_report(
    rep: ImportReport, rejected: Rejected, read: dict[str, int], dry_run: bool
) -> None:
    head = "Пробный прогон (в БД ничего не записано)" if dry_run else "Итог занесения"
    print(f"{head}:")
    print(f"  прочитано записей : {sum(read.values())} ({_pairs(read)})")
    print(f"  {'было бы добавлено' if dry_run else 'добавлено'}  : {rep.added}")
    print(f"  уже было          : {rep.existed}")
    if rep.existed:
        print(f"    из них источник дописан: {rep.sources_extended}")
        print(f"    из них связь с threads усилена (guessed → confirmed): {rep.confidence_raised}")
    rejected.print()
    if rep.by_source:
        print("  по первому источнику в пакете:")
        for src, c in sorted(rep.by_source.items()):
            print(f"    {src:<8} добавлено {c['added']}, уже было {c['existed']}")


def _pairs(d: dict[str, int]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in d.items())


async def run_add(conn: asyncpg.Connection, args: argparse.Namespace, st: IgSettings) -> None:
    rejected = Rejected()
    found, read = await gather(conn, args, rejected)
    cands = collapse(found)
    if args.dry_run:
        rep = await add_candidates(conn, cands, dry_run=True)
        print_report(rep, rejected, read, True)
        return
    run_id = await start_run(conn, "import", _run_settings(args))
    try:
        rep = await add_candidates(conn, cands)
    except BaseException as e:
        await finish_run(conn, run_id, "error", f"{type(e).__name__}: {e}")
        raise
    await finish_run(conn, run_id, "ok")
    print_report(rep, rejected, read, False)
    print(f"  запуск            : runs#{run_id} (kind=import)")


def _run_settings(args: argparse.Namespace) -> dict:
    """Параметры импорта для runs.settings (только заданные флаги)."""
    keys = (
        "file",
        "from_threads",
        "threads_confidence",
        "threads_target_only",
        "threads_location",
        "threads_min_followers",
        "threads_category",
        "limit",
    )
    out = {k: getattr(args, k) for k in keys if getattr(args, k)}
    if args.file:
        out["file"] = args.file.name
    if args.nicks:
        out["nicks"] = len(args.nicks)
    return out


async def run_list(conn: asyncpg.Connection, args: argparse.Namespace, st: IgSettings) -> None:
    rows = await list_profiles(conn, args.source, args.status, args.limit)
    if not rows:
        print("Профилей нет (занести: cli.py ig profiles add …)")
        return
    head = ("id", "ник", "источники", "статус", "обраб.", "связь", "подп.")
    widths = (6, 30, 18, 10, 8, 10, 0)
    print("  ".join(f"{h:<{w}}" for h, w in zip(head, widths)).rstrip())
    for r in rows:
        print(
            f"{r['id']:<6}  {r['username']:<30} {','.join(r['sources']):<18} {r['status']:<10} "
            f"{r['processed_mode'] or '—':<8} {r['link_confidence'] or '—':<10} "
            f"{r['followers_count'] if r['followers_count'] is not None else '—'}"
        )
    print(f"Показано {len(rows)} (новые сверху; --limit для большего)")


async def run_stats(conn: asyncpg.Connection, args: argparse.Namespace, st: IgSettings) -> None:
    stats = await profile_stats(conn)
    print(f"Профилей {stats.pop('total')[0][1]}")
    for title, rows in stats.items():
        print(f"\n{title}:")
        for key, n in rows:
            print(f"  {key:<22} {n}")


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    if args.cmd == "add":
        check_add_args(ap, args)
    st = load_ig_settings()
    runner = {"add": run_add, "list": run_list, "stats": run_stats}[args.cmd]
    run_sync(st, lambda conn: runner(conn, args, st), schema=IG_SCHEMA)
