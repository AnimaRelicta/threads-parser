#!/usr/bin/env python3
"""Управление аккаунтами Threads: вход, реестр, перенос кук на сервер.

Реестр лежит в data/accounts.json, куки каждого аккаунта — в data/cookies/<имя>.json.
Вход всегда выполняется в браузере с окном: вы входите вручную (капча, 2FA).

Команды (из корня проекта):
    uv run scripts/accounts.py add 3            # завести 3 аккаунта (окна по очереди)
    uv run scripts/accounts.py list
    uv run scripts/accounts.py relogin account_002
    uv run scripts/accounts.py check [--headless]
    uv run scripts/accounts.py export           # куки одним zip для переноса на сервер
    uv run scripts/accounts.py import bundle.zip     # zip, папка или один файл кук

Перенос на сервер (где нельзя открыть окно): у себя `add N` и `export`, на сервере
`import <архив>` (или просто скопировать файлы в data/cookies/: парсер сам заведёт их
в реестре). Куки равноценны паролю: храните архив как секрет.
"""

import argparse
import asyncio
from pathlib import Path

from config import load_settings
from registry.commands import cmd_add, cmd_check, cmd_export, cmd_import, cmd_list, cmd_relogin


def main() -> None:
    ap = argparse.ArgumentParser(description="Аккаунты Threads: вход, реестр, перенос кук")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="завести N новых аккаунтов (вход вручную, окна по очереди)")
    a.add_argument("count", type=int)
    a.add_argument("--prefix", default="account")
    a.add_argument("--yes", "-y", action="store_true")

    sub.add_parser("list", help="показать аккаунты")

    r = sub.add_parser("relogin", help="войти заново в существующие аккаунты")
    r.add_argument("names", nargs="+")

    c = sub.add_parser("check", help="проверить, что сессии действуют")
    c.add_argument("names", nargs="*")
    c.add_argument("--headless", action="store_true", help="проверять без окна")

    e = sub.add_parser("export", help="выгрузить куки одним zip для переноса на сервер")
    e.add_argument("names", nargs="*", help="по умолчанию все")
    e.add_argument("--out", type=Path)

    i = sub.add_parser("import", help="добавить аккаунты из zip, папки или файла кук")
    i.add_argument("file", type=Path)
    i.add_argument("--name", help="имя аккаунта (по умолчанию следующее свободное)")
    i.add_argument(
        "--user-agent",
        help="User-Agent браузера, из которого выгружены куки (для файла без user_agent)",
    )
    i.add_argument("--yes", "-y", action="store_true")

    args = ap.parse_args()
    st = load_settings()
    try:
        if args.cmd == "add":
            if args.count < 1:
                raise SystemExit("Количество должно быть больше нуля")
            asyncio.run(cmd_add(st, args.count, args.prefix, args.yes))
        elif args.cmd == "list":
            cmd_list(st)
        elif args.cmd == "relogin":
            asyncio.run(cmd_relogin(st, args.names))
        elif args.cmd == "check":
            asyncio.run(cmd_check(st, args.names, args.headless))
        elif args.cmd == "export":
            cmd_export(st, args.names, args.out)
        elif args.cmd == "import":
            cmd_import(st, args.file, args.name, args.yes, args.user_agent)
    except KeyboardInterrupt:
        print("\n[!] Остановлено пользователем.")


if __name__ == "__main__":
    main()
