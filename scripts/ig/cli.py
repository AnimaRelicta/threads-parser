#!/usr/bin/env python3
"""Точка входа Instagram-пакета: `cli.py ig <подкоманда>`.

Примеры (из корня проекта):
    uv run scripts/cli.py ig db migrate         # схема instagram и миграции
    uv run scripts/cli.py ig db status          # применённые миграции, строки в таблицах
    uv run scripts/cli.py ig accounts add 2     # аккаунты Instagram: add, list, check, import ...
    uv run scripts/cli.py ig profiles add --file data/ig_profiles.txt   # занести профили в БД
    uv run scripts/cli.py ig parse --profile-only --source all          # метаданные профилей

Остальные подкоманды (export, sql) и режимы parse добавляются по шагам ТЗ.
Подкоманда получает остаток командной строки без изменений.
"""

import importlib
import sys

COMMANDS = {
    "db": ("ig.db.admin", "схема instagram: migrate, status"),
    "accounts": ("ig.registry.cli", "аккаунты: add, list, relogin, check, proxy, import, export"),
    "profiles": ("ig.profiles_cli", "профили: add (файл, ники, threads), list, stats"),
    "parse": ("ig.parse_cli", "парсинг: --profile-only (метаданные профилей)"),
}


def usage() -> str:
    lines = ["Использование: cli.py ig <команда> [аргументы]", "", "Команды:"]
    lines += [f"  {name:<9} {desc}" for name, (_, desc) in COMMANDS.items()]
    lines += ["", "Справка по команде: cli.py ig <команда> --help"]
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
    sys.argv = [f"cli.py ig {cmd}", *args[1:]]
    module.main()


if __name__ == "__main__":
    main()
