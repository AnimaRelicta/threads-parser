#!/usr/bin/env python3
"""Управление базой Postgres: миграции и состояние.

Команды (из корня проекта):
    uv run scripts/dbadmin.py migrate     # создать схему threads и применить миграции
    uv run scripts/dbadmin.py status      # применённые миграции и число строк в таблицах
    uv run scripts/dbadmin.py import-accounts   # перенести аккаунты из data/accounts.json
    uv run scripts/cli.py db migrate      # то же через единую точку входа

Строка подключения берётся из DATABASE_URL в scripts/.env.
"""

import argparse
import json
from pathlib import Path

from config import load_settings
from db.accounts import import_legacy
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


def cmd_import_accounts(st, file: Path | None) -> None:
    """Разовый перенос старого реестра: только записи аккаунтов, без счётчиков и runs[]."""
    path = file or st.accounts_file
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path}: не JSON ({e})")
    if not isinstance(reg, dict):
        raise SystemExit(f"{path}: ожидался объект {{имя: запись}}")
    added, skipped = run_sync(st, lambda conn: import_legacy(conn, reg))
    print(f"[+] Аккаунтов добавлено: {added}, уже были в БД: {skipped}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Postgres: миграции и состояние")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="применить недостающие миграции")
    sub.add_parser("status", help="показать применённые миграции и размеры таблиц")
    imp = sub.add_parser("import-accounts", help="перенести аккаунты из accounts.json в БД")
    imp.add_argument("--file", type=Path, help="по умолчанию ACCOUNTS_FILE из .env")
    args = ap.parse_args()
    st = load_settings()
    try:
        if args.cmd == "import-accounts":
            cmd_import_accounts(st, args.file)
        else:
            {"migrate": cmd_migrate, "status": cmd_status}[args.cmd](st)
    except KeyboardInterrupt:
        print("\n[!] Остановлено пользователем.")


if __name__ == "__main__":
    main()
