"""Подкоманда `cli.py ig parse`: запуск парсера Instagram.

    uv run scripts/cli.py ig parse --profile-only --source all --only-unprocessed --headless
    uv run scripts/cli.py ig parse --profile-only ник1 @ник2 --file data/ig_profiles.txt
    uv run scripts/cli.py ig parse --profile-only --source new --dry-run

    uv run scripts/cli.py ig parse --posts-only --source all --since 30d --headless
    uv run scripts/cli.py ig parse --posts-only --source new --limit 50 --min-posts 20

    uv run scripts/cli.py ig parse --source all --since 30d --comments --images --headless
    uv run scripts/cli.py ig parse --fill --source all --comments --videos

Режимы: --profile-only (метаданные), --posts-only (лента без комментариев и медиа),
--fill (добор постов, собранных --posts-only), без флага режима — полный проход: посты и то,
что включено флагами содержимого (--comments, --images, --videos, --avatars).
Флаги сильнее значений IG_* из scripts/.env.
"""

import argparse
import sys
from pathlib import Path

from core.timeutil import parse_since

from config import IgSettings, load_ig_settings


def _yn(v: bool) -> str:
    return "вкл по умолчанию" if v else "выкл по умолчанию"


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

    mode.add_argument(
        "--fill", action="store_true", help="добрать посты, собранные --posts-only (без ленты)"
    )

    content = ap.add_argument_group("содержимое (полный проход и --fill)")
    bool_opt = argparse.BooleanOptionalAction
    content.add_argument("--comments", action=bool_opt, help=f"комментарии ({_yn(st.comments)})")
    content.add_argument("--depth", type=int, help=f"1 = верхний уровень, 2 = + ответы ({st.depth})")
    content.add_argument(
        "--max-comments", type=int, help=f"лимит комментариев на пост, 0 = все ({st.max_comments})"
    )
    content.add_argument("--images", action=bool_opt, help=f"фото и кадры каруселей ({_yn(st.images)})")
    content.add_argument("--videos", action=bool_opt, help=f"видео и рилсы ({_yn(st.videos)})")
    content.add_argument("--avatars", action=bool_opt, help=f"аватар профиля ({_yn(st.avatars)})")
    content.add_argument(
        "--recheck-days", type=int, help=f"посты младше N дней перепроверять, 0 = выкл ({st.recheck_days})"
    )
    content.add_argument(
        "--full-rescan", action="store_true", help="не останавливаться на знакомых комментариях"
    )

    feed = ap.add_argument_group("лента (--posts-only и полный проход)")
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
    feed_flags = [
        n
        for n, v in (
            ("--since", args.since),
            ("--limit", args.limit),
            ("--full", args.full),
            ("--stop-after", args.stop_after),
            ("--min-posts", args.min_posts),
        )
        if v
    ]
    walks_feed = args.posts_only or not (args.profile_only or args.fill)
    if feed_flags and not walks_feed:
        ap.error(f"{', '.join(feed_flags)} работают только с лентой (--posts-only и полный проход)")
    if args.min_posts and not args.posts_only:
        ap.error("--min-posts работает только с --posts-only")
    if not args.profile_only and (args.no_about or args.refresh_about):
        ap.error("--no-about и --refresh-about относятся к --profile-only")
    content = [
        n
        for n, v in (
            ("--comments", args.comments),
            ("--depth", args.depth),
            ("--max-comments", args.max_comments),
            ("--images", args.images),
            ("--videos", args.videos),
            ("--avatars", args.avatars),
            ("--recheck-days", args.recheck_days),
            ("--full-rescan", args.full_rescan),
        )
        if v is not None and v is not False
    ]
    if content and (args.profile_only or args.posts_only):
        print(f"[!] {', '.join(content)}: флаги содержимого в этом режиме игнорируются", file=sys.stderr)
    if args.depth is not None and args.depth not in (1, 2):
        ap.error("--depth: 1 или 2 (потолок Instagram — комментарий и ответы)")
    for name, v in (("--max-comments", args.max_comments), ("--recheck-days", args.recheck_days)):
        if v is not None and v < 0:
            ap.error(f"{name} не может быть отрицательным")
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
    args.comments = st.comments if args.comments is None else args.comments
    args.depth = args.depth or st.depth
    args.max_comments = st.max_comments if args.max_comments is None else args.max_comments
    args.images = st.images if args.images is None else args.images
    args.videos = st.videos if args.videos is None else args.videos
    args.avatars = st.avatars if args.avatars is None else args.avatars
    args.recheck_days = st.recheck_days if args.recheck_days is None else args.recheck_days
    args.storage_dir = st.storage_dir
    args.min_free_gb = st.min_free_gb
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
