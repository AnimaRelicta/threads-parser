#!/usr/bin/env python3
"""Точка входа: связь профилей Threads с Instagram (таблица instagram_links). Только БД.

Связь ставится при открытии профиля (parse): ссылка/иконка Instagram в шапке (header_link),
JSON страницы или ссылка в био (threads_json), иначе догадка «тот же ник» (username_guess,
confidence=guessed, не проверена).

Примеры (из корня проекта):
    uv run scripts/cli.py instagram stats                    # сколько профилей, по источникам
    uv run scripts/cli.py instagram list --confidence guessed --limit 20
    uv run scripts/cli.py instagram list --source header_link --csv data/export/ig.csv
"""

import argparse
import csv
from pathlib import Path

from config import ENV_FILE, load_settings
from db.pool import run_sync

SOURCES = ("header_link", "threads_json", "username_guess")

STATS_SQL = """
SELECT l.source, l.confidence, count(*) AS n,
       count(*) FILTER (WHERE lower(l.instagram_username) = lower(p.username)) AS same_nick
FROM instagram_links l JOIN profiles p ON p.id = l.profile_id
GROUP BY l.source, l.confidence ORDER BY l.source
"""
TOTALS_SQL = """
SELECT count(*) AS profiles,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM instagram_links l WHERE l.profile_id = p.id))
           AS linked
FROM profiles p
"""
LIST_SQL = """
SELECT p.username, l.instagram_username, l.source, l.confidence, l.matched_raw, l.updated_at
FROM instagram_links l JOIN profiles p ON p.id = l.profile_id
WHERE ($1::text IS NULL OR l.source = $1) AND ($2::text IS NULL OR l.confidence = $2)
ORDER BY l.updated_at DESC, p.username
LIMIT $3
"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Связь Threads ↔ Instagram. Подключение к БД: {ENV_FILE}"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="сколько профилей со связью, по источникам")
    ls = sub.add_parser("list", help="список связей (для ручной проверки)")
    ls.add_argument("--source", choices=SOURCES, help="только этот источник")
    ls.add_argument("--confidence", choices=("confirmed", "guessed"), help="только эта надёжность")
    ls.add_argument(
        "--limit", type=int, default=30, help="сколько строк, 0 = все (по умолчанию 30)"
    )
    ls.add_argument("--csv", type=Path, metavar="ФАЙЛ", help="записать в CSV вместо вывода")
    return ap


async def _stats(conn) -> tuple:
    return await conn.fetchrow(TOTALS_SQL), await conn.fetch(STATS_SQL)


def cmd_stats(st) -> None:
    totals, rows = run_sync(st, _stats)
    print(f"[*] Профилей в БД: {totals['profiles']}, со связью: {totals['linked']}")
    print(f"    без связи: {totals['profiles'] - totals['linked']} (откроются при следующем parse)")
    for r in rows:
        print(
            f"  {r['source']:<15} {r['confidence']:<10} {r['n']:>6}"
            f"   (ник совпадает с Threads: {r['same_nick']})"
        )
    guessed = sum(r["n"] for r in rows if r["confidence"] == "guessed")
    if guessed:
        print(f"[!] {guessed} связей угаданы по нику и НЕ проверены: instagram.com/<ник>")


def cmd_list(st, args) -> None:
    limit = args.limit if args.limit > 0 else 1_000_000
    rows = run_sync(st, lambda conn: conn.fetch(LIST_SQL, args.source, args.confidence, limit))
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["threads", "instagram", "instagram_url", "source", "confidence", "raw"])
            for r in rows:
                ig = r["instagram_username"]
                w.writerow(
                    [r["username"], ig, f"https://instagram.com/{ig}", r["source"],
                     r["confidence"], r["matched_raw"] or ""]
                )  # fmt: skip
        print(f"[+] Записано {len(rows)} строк: {args.csv}")
        return
    for r in rows:
        print(
            f"  @{r['username']:<28} -> instagram.com/{r['instagram_username']:<28} "
            f"{r['source']}/{r['confidence']}"
        )
    print(f"[*] Показано: {len(rows)}")


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()
    if args.cmd == "stats":
        cmd_stats(st)
    else:
        cmd_list(st, args)


if __name__ == "__main__":
    main()
