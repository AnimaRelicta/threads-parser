#!/usr/bin/env python3
"""Единая точка входа проекта: подкоманды parse, search, discover, classify, accounts, db.

Примеры (из корня проекта):
    uv run scripts/cli.py parse olx_vetka_astana --limit 5 --comments
    uv run scripts/cli.py search "олх ветка астана" --since 7d
    uv run scripts/cli.py discover --since 7d --dry-run   # новые профили по авторам
    uv run scripts/cli.py geo stats               # где находятся профили (КЗ/УЗ)
    uv run scripts/cli.py spam --check            # найти спам-профили (без --apply не банит)
    uv run scripts/cli.py export spam-classes --since 7d   # выгрузка из БД в xlsx
    uv run scripts/cli.py classify --limit 50      # категории профилей через LLM
    uv run scripts/cli.py jev --dry-run            # профили КЗ для Jev: сколько подходит
    uv run scripts/cli.py jev --all                # полный прогон через Jev
    uv run scripts/cli.py jev-posts --dry-run      # посты авторов КЗ для Jev: сколько подходит
    uv run scripts/cli.py jev-posts --all          # полный прогон постов через Jev
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
    "instagram": ("instagram", "связь Threads ↔ Instagram: stats, list, --csv (instagram.py)"),
    "geo": ("geo", "страна/город профилей (КЗ, УЗ): rescan, stats, list (geo.py)"),
    "spam": ("spam", "спам-профили: проверка по БД, список банов, разбан (spam.py)"),
    "export": ("export", "выгрузка из БД в xlsx: базовые и свои запросы (export.py)"),
    "classify": ("classify.cli", "категории профилей через LLM (classify/cli.py)"),
    "jev": (
        "classify.jev_cli",
        "категории профилей КЗ через Jev: --dry-run, --all (classify/jev_cli.py)",
    ),
    "jev-posts": (
        "classify.jev_posts_cli",
        "категории постов авторов КЗ через Jev (classify/jev_posts_cli.py)",
    ),
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
