#!/usr/bin/env python3
"""Точка входа: страна/город профилей (КЗ, УЗ). Только БД, в Threads не ходит.

Источники по убыванию надёжности: about_location («Об этом профиле») > телефон WhatsApp >
био > имя из about > темы шапки. Города в текстах веток (post_text) — слабый сигнал: в итог
не входят, показываются отдельно. Если надёжные источники называют разные страны, итог берётся
из самого надёжного, а строка помечается КОНФЛИКТ и показывает оба источника.

Примеры (из корня проекта):
    uv run scripts/cli.py geo rescan                 # пересчитать всё по данным в БД
    uv run scripts/cli.py geo stats
    uv run scripts/cli.py geo list --country KZ --city Алматы
    uv run scripts/cli.py geo list --csv data/export/geo.csv
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

from config import ENV_FILE, load_settings
from core.geo import DETECTOR_VERSION, find_locations
from db import geo as repo
from db.pool import run_sync

TOP = 15


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=f"Страна/город профилей (КЗ, УЗ). Подключение к БД: {ENV_FILE}"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rescan", help="пересчитать локации по данным в БД (about/шапка/био/посты)")
    sub.add_parser("stats", help="топ городов и стран, профили без данных, источники")
    ls = sub.add_parser("list", help="профили с итоговой страной/городом и источником")
    ls.add_argument("--country", choices=("KZ", "UZ", "OTHER"), help="только эта итоговая страна")
    ls.add_argument("--city", help="только этот итоговый город (любое написание из справочника)")
    ls.add_argument("--limit", type=int, default=30, help="сколько строк, 0 = все (по умолч. 30)")
    ls.add_argument("--csv", type=Path, metavar="ФАЙЛ", help="записать в CSV вместо вывода")
    return ap


def cmd_rescan(st) -> None:
    print(f"[*] Пересчёт локаций, справочник v{DETECTOR_VERSION}")
    res = run_sync(st, repo.rescan)
    print(f"[+] Профилей: {res.profiles}, постов: {res.posts}, найдено упоминаний: {res.mentions}")
    for e in res.errors[:10]:
        print(f"[!] {e}")
    if res.errors:
        print(f"[!] Ошибок: {len(res.errors)} (остальное пересчитано)")


def _place(country: str | None, city: str | None) -> str:
    return f"{country} {city}" if city else (country or "—")


async def _stats(conn):
    return (
        await repo.load_profile_geo(conn),
        await repo.post_totals(conn),
        await repo.source_totals(conn),
    )


def cmd_stats(st) -> None:
    profiles, posts, sources = run_sync(st, _stats)
    found = [p for p in profiles if p.resolved.country]
    print(f"[*] Профилей: {len(profiles)}, с геоданными: {len(found)}")
    print(f"    без геоданных: {len(profiles) - len(found)} (about не собран или сигналов нет)")
    conflicts = sum(p.resolved.conflict for p in profiles)
    print(f"    с конфликтом источников: {conflicts}")
    print("\nПо профилям (итоговая страна):")
    for c, n in Counter(p.resolved.country for p in found).most_common():
        print(f"  {c:<6} {n:>6}")
    print(f"\nПо профилям, топ городов (итог, КЗ/УЗ), до {TOP}:")
    cities = Counter(_place(p.resolved.country, p.resolved.city) for p in found if p.resolved.city)
    for place, n in cities.most_common(TOP):
        print(f"  {place:<26} {n:>6}")
    print("\nПо профилям, откуда взят итог:")
    for s, n in Counter(p.resolved.source for p in found).most_common():
        print(f"  {s:<16} {n:>6}")
    print(f"\nПо постам (post_text; слабый сигнал), топ до {TOP}:")
    for r in posts[:TOP]:
        place = _place(r["country"], r["city"])
        print(f"  {place:<26} постов {r['n']:>6}, профилей {r['profiles']}")
    if not posts:
        print("  пока нет (geo rescan или новый parse)")
    print("\nУпоминания по источникам (сущностей / профилей):")
    for r in sources:
        print(f"  {r['source']:<16} {r['entities']:>6} / {r['profiles']}")


def _row(p: repo.ProfileGeo) -> dict:
    r = p.resolved
    return {
        "username": p.username,
        "country": r.country or "",
        "city": r.city or "",
        "source": r.source or "",
        "conflict": "КОНФЛИКТ" if r.conflict else "",
        "all_sources": "; ".join(f"{s}={v}" for s, v in r.by_source.items()),
        "posts": "; ".join(f"{_place(c, ct)}×{n}" for c, ct, n in p.posts[:3]),
    }


def cmd_list(st, args) -> None:
    profiles = run_sync(st, repo.load_profile_geo)
    city = args.city
    if city:  # «алматы», «Almaty», «в Алматы» -> канонический город справочника
        found = find_locations(city)
        city = found[0].city if found and found[0].city else city
    rows = [
        p
        for p in profiles
        if p.resolved.country
        and (not args.country or p.resolved.country == args.country)
        and (not city or (p.resolved.city or "").casefold() == city.casefold())
    ]
    if args.limit > 0 and not args.csv:
        rows = rows[: args.limit]
    data = [_row(p) for p in rows]
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]) if data else ["username"])
            w.writeheader()
            w.writerows(data)
        print(f"[+] Записано {len(data)} строк: {args.csv}")
        return
    for d in data:
        print(
            f"  @{d['username']:<28} {_place(d['country'], d['city']):<22} "
            f"{d['source']:<15} {d['conflict']}"
        )
        if d["conflict"]:
            print(f"      источники: {d['all_sources']}")
        if d["posts"]:
            print(f"      в постах: {d['posts']}")
    print(f"[*] Показано: {len(data)}")


def main() -> None:
    st = load_settings()
    args = build_parser().parse_args()
    if args.cmd == "rescan":
        cmd_rescan(st)
    elif args.cmd == "stats":
        cmd_stats(st)
    else:
        cmd_list(st, args)


if __name__ == "__main__":
    main()
