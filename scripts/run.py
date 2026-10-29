#!/usr/bin/env python3
"""Точка входа: парсинг профилей Threads.

Примеры (из корня проекта):
    uv run scripts/run.py olx_vetka_astana --limit 5
    uv run scripts/run.py --since 1m --comments          # профили из data/threads_profiles.txt
    uv run scripts/run.py olx_vetka_astana --threads-only --since 7d   # только список веток
    uv run scripts/run.py olx_vetka_astana --fill-threads --since 7d --comments   # разбор этих веток
    uv run scripts/run.py --source new --threads-only --since 7d       # профили без веток
    uv run scripts/run.py --comments --depth 3 --accounts account_001,account_002 --workers 2
    uv run scripts/run.py --headless                      # на сервере, по готовым кукам
"""

import argparse
import sys

from config import ENV_FILE, load_settings
from scrape.dispatch import run


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Парсер Threads через Playwright. Настройки по умолчанию: {ENV_FILE}"
    )
    ap.add_argument("username", nargs="?", help="имя профиля, @имя или ссылка")
    ap.add_argument(
        "--file",
        help="файл со списком профилей (по умолчанию PROFILES_FILE, "
        "если профиль не указан в командной строке)",
    )
    ap.add_argument(
        "--threads-only",
        action="store_true",
        help="быстрый сбор веток: пройти ленту профиля и сохранить ветки за период "
        "(текст, дата, счётчики) без открытия самих веток, комментариев и медиа",
    )
    ap.add_argument(
        "--fill-threads",
        action="store_true",
        help="разобрать ветки, ранее собранные через --threads-only (открыть ветку, "
        "текст, медиа, посты под веткой по --comments/--depth); обход ленты не делается",
    )
    ap.add_argument(
        "--source",
        help="откуда взять профили из БД: all (is_target), new (ещё без веток), "
        "search, authors (после шага «поиск по авторам») или ники через запятую; "
        "добавляется к профилю из командной строки и к --file",
    )
    ap.add_argument(
        "--accounts",
        type=lambda v: [a.strip() for a in v.split(",") if a.strip()],
        default=st.accounts,
        help="аккаунты через запятую (ACCOUNTS; по умолчанию все активные)",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=st.workers,
        help="воркеров на аккаунт, m: пар вкладок (лента + посты) в одном браузере "
        "аккаунта, у каждой пары свой профиль (WORKERS_PER_ACCOUNT)",
    )
    ap.add_argument(
        "--since",
        default=st.since,
        help="только посты не старше периода: 30d, 4w, 1m (календарный месяц), "
        "1y или дата 2026-09-01 (SINCE)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=st.limit,
        help="сколько подходящих постов на профиль, 0 = все (LIMIT)",
    )
    ap.add_argument(
        "--comments",
        action=argparse.BooleanOptionalAction,
        default=st.comments,
        help="собирать ответы к постам деревом, глубину задаёт --depth "
        "(COMMENTS; --no-comments отключает значение из .env)",
    )
    ap.add_argument(
        "--depth",
        type=int,
        default=st.depth,
        help="глубина комментариев: 1 = ответы на пост, 2 = и ответы на "
        "ответы, и т. д.; -1 = вся вложенность (DEPTH)",
    )
    ap.add_argument(
        "--max-comments",
        type=int,
        default=st.max_comments,
        help="максимум комментариев на пост, 0 = все (MAX_COMMENTS)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="полный проход по окну, игнорируя якорь (иначе, если у профиля "
        "есть anchor.json, обход останавливается после --stop-after "
        "подряд известных постов)",
    )
    ap.add_argument(
        "--stop-after",
        type=int,
        default=st.stop_after_known,
        help="сколько подряд известных постов останавливают обход, когда "
        "есть якорь (STOP_AFTER_KNOWN)",
    )
    ap.add_argument(
        "--recheck-days",
        type=int,
        default=st.recheck_days,
        help="посты младше N дней проверяются на новые комментарии при "
        "каждом запуске на всю глубину; более старые только если "
        "счётчик ответов вырос, 0 = отключить (RECHECK_DAYS)",
    )
    ap.add_argument(
        "--full-rescan",
        action="store_true",
        help="не останавливаться на уже известном комментарии, а заново просматривать все ответы",
    )
    ap.add_argument(
        "--with-videos",
        action=argparse.BooleanOptionalAction,
        default=st.with_videos,
        help="брать также посты с видео, по умолчанию только текст и фото (WITH_VIDEOS)",
    )
    ap.add_argument(
        "--no-block",
        action="store_true",
        help="не блокировать картинки/видео/шрифты на вкладке постов",
    )
    ap.add_argument(
        "--delay", type=float, default=st.delay, help="базовая пауза между постами, сек (DELAY)"
    )
    ap.add_argument(
        "--profile-delay",
        type=float,
        default=st.profile_delay,
        help="базовая пауза между профилями, сек (PROFILE_DELAY)",
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
        help="подробный лог и отладочные файлы (DEBUG)",
    )
    ap.add_argument("--output", default=str(st.output_dir), help="папка результатов (OUTPUT_DIR)")
    return ap


# Флаги про содержимое ветки: в режиме --threads-only не используются
THREADS_ONLY_IGNORED = (
    "--comments",
    "--no-comments",
    "--depth",
    "--max-comments",
    "--with-videos",
    "--no-with-videos",
    "--recheck-days",
    "--full-rescan",
    "--no-block",
)


def warn_ignored_flags(argv: list[str]) -> None:
    """Предупреждает о явно переданных флагах, которые --threads-only игнорирует."""
    used = [f for f in THREADS_ONLY_IGNORED if any(a == f or a.startswith(f + "=") for a in argv)]
    if used:
        print(f"[!] --threads-only: флаги игнорируются: {', '.join(used)}")


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()
    if args.stop_after < 1:
        raise SystemExit("--stop-after должен быть не меньше 1")
    if args.threads_only and args.fill_threads:
        raise SystemExit("--threads-only и --fill-threads несовместимы: это два разных шага")
    if not args.username and not args.file and not args.source:
        args.file = str(st.profiles_file)
    if args.threads_only:
        warn_ignored_flags(sys.argv[1:])
    run(args, st)


if __name__ == "__main__":
    main()
