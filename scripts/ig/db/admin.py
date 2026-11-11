"""Команды `ig db`: миграции схемы instagram и состояние."""

import argparse

from config import IgSettings, load_ig_settings
from db.migrate import migrate, status
from db.pool import run_sync
from ig.consts import IG_LOCK_ID, IG_MIGRATIONS_DIR, IG_SCHEMA


def cmd_migrate(st: IgSettings) -> None:
    done = run_sync(
        st,
        lambda conn: migrate(conn, IG_SCHEMA, IG_MIGRATIONS_DIR, IG_LOCK_ID),
        schema=IG_SCHEMA,
    )
    if done:
        print(f"[+] Применено миграций: {len(done)}: {', '.join(done)}")
    else:
        print("[*] Схема актуальна, применять нечего")


def cmd_status(st: IgSettings) -> None:
    info = run_sync(st, lambda conn: status(conn, IG_SCHEMA, IG_MIGRATIONS_DIR), schema=IG_SCHEMA)
    print(f"Применено: {', '.join(info['applied']) or '—'}")
    print(f"Ожидает  : {', '.join(info['pending']) or '—'}")
    if info["unknown"]:
        print(f"[!] В БД есть версии без файла миграции: {', '.join(info['unknown'])}")
    if info["tables"]:
        width = max(len(t) for t in info["tables"])
        print("\nСтроки в таблицах:")
        for t, n in info["tables"].items():
            print(f"  {t:<{width}}  {n}")
    elif info["pending"]:
        print("\nСхема не создана: uv run scripts/cli.py ig db migrate")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="cli.py ig db", description="База Postgres, схема instagram: migrate, status"
    )
    ap.add_argument("action", choices=("migrate", "status"))
    args = ap.parse_args()
    st = load_ig_settings()
    (cmd_migrate if args.action == "migrate" else cmd_status)(st)
