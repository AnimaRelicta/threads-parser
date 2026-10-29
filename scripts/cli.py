#!/usr/bin/env python3
"""Единая точка входа проекта: подкоманды parse, search, discover, accounts, db.

Примеры (из корня проекта):
    uv run scripts/cli.py parse olx_vetka_astana --limit 5 --comments
    uv run scripts/cli.py search "олх ветка астана" --since 7d
    uv run scripts/cli.py discover --since 7d --dry-run   # новые профили по авторам
    uv run scripts/cli.py accounts check --headless
    uv run scripts/cli.py db migrate          # схема Postgres
    uv run scripts/cli.py parse --help        # справка подкоманды

Подкоманда получает остаток командной строки без изменений, поэтому все флаги те же, что у
`run.py`, `search.py`, `accounts.py` и `dbadmin.py`; эти файлы по-прежнему можно запускать напрямую.
"""

import importlib
import sys

COMMANDS = {
    "parse": ("run", "парсинг профилей: посты, медиа, комментарии (run.py)"),
    "search": ("search", "поиск по Threads: посты по запросам (search.py)"),
    "discover": ("discover", "новые профили по авторам постов и веток (discover.py)"),
    "accounts": ("accounts", "аккаунты: add, list, relogin, check, export, import (accounts.py)"),
    "db": ("dbadmin", "база Postgres: migrate, status (dbadmin.py)"),
}


def usage() -> str:
    lines = ["Использование: cli.py <команда> [аргументы]", "", "Команды:"]
    lines += [f"  {name:<9} {desc}" for name, (_, desc) in COMMANDS.items()]
    lines += ["", "Справка по команде: cli.py <команда> --help"]
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(usage())
        return
    cmd = args[0]
    if cmd not in COMMANDS:
        raise SystemExit(f"Неизвестная команда «{cmd}».\n\n{usage()}")
    module = importlib.import_module(COMMANDS[cmd][0])
    sys.argv = [f"cli.py {cmd}", *args[1:]]
    module.main()


if __name__ == "__main__":
    main()
