#!/usr/bin/env python3
"""Точка входа: спам-профили. Проверка уже собранных веток, список банов, разбан.

Работает только с БД, без браузера. Во время парсинга (--threads-only, полный проход)
проверка идёт сама; этот скрипт нужен, чтобы прогнать профили, собранные раньше, и
разобрать спорные случаи.

Примеры (из корня проекта):
    uv run scripts/spam.py --check                    # пробный прогон: кого забанил бы
    uv run scripts/spam.py --check --apply            # забанить найденных
    uv run scripts/spam.py --check --source authors   # только профили, найденные discover
    uv run scripts/spam.py --check adina.rahatt       # один профиль, подробные сигналы
    uv run scripts/spam.py --list                     # кто в бане и почему
    uv run scripts/spam.py --unban adina.rahatt       # снять бан и больше не банить
"""

import argparse
import json

from config import ENV_FILE, load_settings
from core.fsio import normalize_username
from core.spam import RULES, rules_with
from db import spam as spam_repo
from db.pool import run_sync
from db.profiles import list_targets


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Спам-профили. Настройки подключения к БД: {ENV_FILE}"
    )
    ap.add_argument("username", nargs="?", help="проверить один профиль (@ник или ссылка)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="проверить профили по веткам из БД")
    mode.add_argument("--list", action="store_true", help="показать забаненные профили")
    mode.add_argument("--unban", metavar="НИК", help="снять бан и внести в белый список")
    ap.add_argument(
        "--source",
        default="all",
        help="какие профили проверять: all | new | search | authors | ники через запятую "
        "(по умолчанию all, забаненные не берутся)",
    )
    ap.add_argument(
        "--apply", action="store_true", help="записать вердикты и забанить (без флага только показ)"
    )
    ap.add_argument("--show", type=int, default=30, help="сколько строк списка вывести, 0 = все")
    return ap


async def _check(conn, names: list[str], apply: bool, show: int, verbose: bool, rules):
    rows = await conn.fetch(
        "SELECT id, username FROM profiles WHERE lower(username) = ANY($1::text[]) ORDER BY id",
        [n.lower() for n in names],
    )
    stats = {"checked": 0, "spam": 0, "few": 0}
    flagged = []
    for r in rows:
        v = await spam_repo.check_profile(conn, r["id"], apply=apply, rules=rules)
        if v.sample < RULES.min_posts:
            stats["few"] += 1
            continue
        stats["checked"] += 1
        if v.is_spam:
            stats["spam"] += 1
            flagged.append((r["username"], v))
        elif verbose:
            print(f"  @{r['username']}: не спам, баллы {v.score}, сигналы {list(v.signals)}")
    for name, v in flagged if show <= 0 else flagged[:show]:
        print(f"  @{name:<30} баллы {v.score:<4} {v.reason}")
        if verbose:
            print("    " + json.dumps(v.metrics, ensure_ascii=False))
    if show > 0 and len(flagged) > show:
        print(f"  ... и ещё {len(flagged) - show}")
    return stats


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()

    if args.unban:
        nick = normalize_username(args.unban)
        ok = run_sync(st, lambda conn: spam_repo.unban(conn, nick))
        print(f"[+] @{nick}: бан снят, в белом списке" if ok else f"[!] Профиль @{nick} не найден")
        return

    if args.list:
        rows = run_sync(
            st,
            lambda conn: conn.fetch(
                "SELECT username, banned_at, spam_score, ban_reason FROM profiles "
                "WHERE is_banned ORDER BY banned_at DESC"
            ),
        )
        shown = rows if args.show <= 0 else rows[: args.show]
        for r in shown:
            print(f"  @{r['username']:<30} {r['banned_at']:%Y-%m-%d %H:%M} {r['ban_reason']}")
        print(f"[*] В бане: {len(rows)}")
        return

    verbose = bool(args.username)
    if args.username:
        nick = normalize_username(args.username)
        if not nick:
            raise SystemExit(f"Не удалось определить профиль из: {args.username}")
        names = [nick]
    else:
        names = run_sync(st, lambda conn: list_targets(conn, args.source))
    if not names:
        raise SystemExit("Нет профилей для проверки")
    rules = rules_with(st.spam_min_followers)
    stats = run_sync(st, lambda conn: _check(conn, names, args.apply, args.show, verbose, rules))
    print(
        f"[*] Проверено: {stats['checked']}, спам: {stats['spam']}, "
        f"мало веток для вывода: {stats['few']}"
    )
    if stats["spam"]:
        print("[+] Забанено" if args.apply else "[*] Без --apply ничего не записано")


if __name__ == "__main__":
    main()
