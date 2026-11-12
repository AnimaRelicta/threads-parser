"""Подкоманда `cli.py ig parse`: запуск парсера Instagram.

    uv run scripts/cli.py ig parse --profile-only --source all --only-unprocessed --headless
    uv run scripts/cli.py ig parse --profile-only ник1 @ник2 --file data/ig_profiles.txt
    uv run scripts/cli.py ig parse --profile-only --source new --dry-run

    uv run scripts/cli.py ig parse --posts-only --source all --since 30d --headless
    uv run scripts/cli.py ig parse --posts-only --source new --limit 50 --min-posts 20

Доступны режимы --profile-only (метаданные профиля) и --posts-only (лента без комментариев
и медиа). Полный проход (комментарии, медиа, --fill) добавляется следующим шагом ТЗ.
Флаги сильнее значений IG_* из scripts/.env.
"""

import argparse
from pathlib import Path

from core.timeutil import parse_since

from config import IgSettings, load_ig_settings


def build_parser(st: IgSettings) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py ig parse",
        description="Парсер Instagram (профили из БД; ники и --file заносятся в БД здесь же)",
    )
    ap.add_argument("nicks", nargs="*", help="ники, @ники, ссылки (заносятся в БД, источник cli)")
    ap.add_argument("--file", type=Path, help="файл со списком: занести (источник file) и парсить")
    ap.add_argument(
        "--source", help="all | new (профили без постов) | ник,ник — из БД, только is_target"
    )
    ap.add_argument("--only-unprocessed", action="store_true", help="пропустить уже обработанные")
    ap.add_argument("--dry-run", action="store_true", help="показать список профилей, без браузера")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--profile-only", action="store_true", help="только метаданные профиля")
    mode.add_argument(
        "--posts-only", action="store_true", help="лента: посты без комментариев и медиа"
    )

    feed = ap.add_argument_group("лента (только с --posts-only)")
    feed.add_argument("--since", help=f"окно: 30d, 4w, 1m, 1y или дата ({st.since or 'без границы'})")
    feed.add_argument("--limit", type=int, help=f"постов на профиль, 0 = все ({st.limit})")
    feed.add_argument("--full", action="store_true", help="игнорировать якорь: пройти окно целиком")
    feed.add_argument(
        "--stop-after",
        type=int,
        help=f"остановка после серии уже известных постов ({st.stop_after_known})",
    )
    feed.add_argument(
        "--min-posts", type=int, default=0, help="добрать до N постов у профиля (по умолчанию 0)"
    )

    ap.add_argument("--no-about", action="store_true", help="не открывать окно «Об аккаунте»")
    ap.add_argument(
        "--refresh-about",
        action="store_true",
        help="собрать «Об аккаунте» заново (по умолчанию один раз на профиль)",
    )
    ap.add_argument("--accounts", help="аккаунты через запятую (по умолчанию все активные)")
    ap.add_argument("--tabs", type=int, help=f"вкладок на аккаунт (по умолчанию {st.tabs})")
    ap.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        help=f"браузер без окна (по умолчанию {'да' if st.headless else 'нет'})",
    )
    ap.add_argument("--delay", type=float, help=f"пауза между запросами, с ({st.delay:g})")
    ap.add_argument(
        "--profile-delay", type=float, help=f"пауза между профилями, с ({st.profile_delay:g})"
    )
    ap.add_argument("--debug", action="store_true", help="подробный вывод")
    ap.add_argument("--output", type=Path, help=f"каталог summary.json ({st.output_dir})")
    return ap


def resolve_args(ap: argparse.ArgumentParser, args: argparse.Namespace, st: IgSettings) -> None:
    """Проверки совместимости и подстановка значений по умолчанию из IgSettings."""
    if not (args.profile_only or args.posts_only):
        ap.error("выберите режим: --profile-only или --posts-only (полный проход — шаг 6)")
    feed_flags = [n for n, v in (("--since", args.since), ("--limit", args.limit),
                  ("--full", args.full), ("--stop-after", args.stop_after),
                  ("--min-posts", args.min_posts)) if v]
    if feed_flags and not args.posts_only:
        ap.error(f"{', '.join(feed_flags)} работают только с --posts-only")
    if args.posts_only and (args.no_about or args.refresh_about):
        ap.error("--no-about и --refresh-about относятся к --profile-only")
    for name, v in (("--limit", args.limit), ("--min-posts", args.min_posts)):
        if v is not None and v < 0:
            ap.error(f"{name} не может быть отрицательным")
    if args.stop_after is not None and args.stop_after < 1:
        ap.error("--stop-after должен быть не меньше 1")
    if not (args.nicks or args.file or args.source):
        ap.error("укажите ники, --file или --source all|new|ник,ник")
    if args.no_about and args.refresh_about:
        ap.error("--no-about и --refresh-about несовместимы")
    if args.tabs is not None and args.tabs < 1:
        ap.error("--tabs должен быть не меньше 1")
    args.since = args.since or st.since
    args.cutoff = parse_since(args.since)  # одно значение на весь запуск
    args.limit = st.limit if args.limit is None else args.limit
    args.stop_after = args.stop_after or st.stop_after_known
    args.tabs = args.tabs or st.tabs
    args.headless = st.headless if args.headless is None else args.headless
    args.delay = st.delay if args.delay is None else args.delay
    args.profile_delay = st.profile_delay if args.profile_delay is None else args.profile_delay
    args.output = args.output or st.output_dir
    args.accounts = (
        [a.strip() for a in args.accounts.split(",") if a.strip()] if args.accounts else st.accounts
    )


def main() -> None:
    st = load_ig_settings()
    ap = build_parser(st)
    args = ap.parse_args()
    resolve_args(ap, args, st)
    from ig.scrape.dispatch import run  # playwright нужен только для запуска, не для --help

    run(args, st)
