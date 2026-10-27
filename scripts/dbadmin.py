#!/usr/bin/env python3
"""Управление базой Postgres: миграции и состояние.

Команды (из корня проекта):
    uv run scripts/dbadmin.py migrate     # создать схему threads и применить миграции
    uv run scripts/dbadmin.py status      # применённые миграции и число строк в таблицах
    uv run scripts/cli.py db migrate      # то же через единую точку входа

Строка подключения берётся из DATABASE_URL в scripts/.env.
"""

import argparse

from config import load_settings
from db.migrate import migrate, status
from db.pool import run_sync


def cmd_migrate(st) -> None:
    done = run_sync(st, migrate)
    if done:
        print(f"[+] Применено миграций: {len(done)}: {', '.join(done)}")
    else:
        print("[*] Схема актуальна, применять нечего")


def cmd_status(st) -> None:
    info = run_sync(st, status)
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
        print("\nСхема не создана: uv run scripts/dbadmin.py migrate")


def main() -> None:
    ap = argparse.ArgumentParser(description="Postgres: миграции и состояние")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="применить недостающие миграции")
    sub.add_parser("status", help="показать применённые миграции и размеры таблиц")
    args = ap.parse_args()
    st = load_settings()
    try:
        {"migrate": cmd_migrate, "status": cmd_status}[args.cmd](st)
    except KeyboardInterrupt:
        print("\n[!] Остановлено пользователем.")


if __name__ == "__main__":
    main()
