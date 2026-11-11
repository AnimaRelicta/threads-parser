"""Подкоманда `cli.py ig accounts`: вход, реестр, перенос кук на сервер.

Реестр лежит в Postgres (схема instagram), куки аккаунта — в data/ig_cookies/<имя>.json.
Вход всегда с окном браузера: вы входите вручную (капча, 2FA).

    uv run scripts/cli.py ig accounts add 3
    uv run scripts/cli.py ig accounts list
    uv run scripts/cli.py ig accounts check [--headless]
    uv run scripts/cli.py ig accounts import-legacy --file ../insta-parser/data/accounts.json \\
        --cookies-dir ../insta-parser/data/cookies
"""

import argparse
import asyncio
from pathlib import Path

from config import load_ig_settings
from ig.registry.commands import (
    cmd_add,
    cmd_check,
    cmd_cooldown,
    cmd_export,
    cmd_import,
    cmd_import_legacy,
    cmd_list,
    cmd_proxy,
    cmd_relogin,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py ig accounts", description="Аккаунты Instagram: вход, реестр, перенос кук"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="завести N новых аккаунтов (вход вручную, окна по очереди)")
    a.add_argument("count", type=int)
    a.add_argument("--prefix", default="account")
    a.add_argument("--yes", "-y", action="store_true")

    sub.add_parser("list", help="показать аккаунты и статистику из БД")

    x = sub.add_parser("proxy", help="прокси аккаунта (без аргументов показывает текущий)")
    x.add_argument("name")
    x.add_argument("url", nargs="?", help="socks5://host:port или http://user:pass@host:port")
    x.add_argument("--clear", action="store_true", help="сбросить: будет общий PROXY из .env")

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
    i.add_argument("--user-agent", help="User-Agent браузера, из которого выгружены куки")
    i.add_argument("--yes", "-y", action="store_true")

    d = sub.add_parser("cooldown", help="пауза аккаунта (как после 429) или её снятие")
    d.add_argument("name")
    d.add_argument("seconds", type=int, nargs="?", help="по умолчанию IG_429_PAUSE")
    d.add_argument("--clear", action="store_true", help="снять паузу")

    lg = sub.add_parser("import-legacy", help="разовый перенос accounts.json из insta-parser")
    lg.add_argument("--file", type=Path, help="accounts.json (по умолчанию IG_ACCOUNTS_FILE)")
    lg.add_argument("--cookies-dir", type=Path, help="папка data/cookies из insta-parser")
    return ap


def main() -> None:
    args = build_parser().parse_args()
    st = load_ig_settings()
    try:
        if args.cmd == "add":
            if args.count < 1:
                raise SystemExit("Количество должно быть больше нуля")
            asyncio.run(cmd_add(st, args.count, args.prefix, args.yes))
        elif args.cmd == "list":
            asyncio.run(cmd_list(st))
        elif args.cmd == "proxy":
            asyncio.run(cmd_proxy(st, args.name, args.url, args.clear))
        elif args.cmd == "relogin":
            asyncio.run(cmd_relogin(st, args.names))
        elif args.cmd == "check":
            asyncio.run(cmd_check(st, args.names, args.headless))
        elif args.cmd == "export":
            asyncio.run(cmd_export(st, args.names, args.out))
        elif args.cmd == "import":
            asyncio.run(cmd_import(st, args.file, args.name, args.yes, args.user_agent))
        elif args.cmd == "cooldown":
            asyncio.run(cmd_cooldown(st, args.name, args.seconds, args.clear))
        elif args.cmd == "import-legacy":
            asyncio.run(cmd_import_legacy(st, args.file, args.cookies_dir))
    except KeyboardInterrupt:
        print("\n[!] Остановлено пользователем.")


if __name__ == "__main__":
    main()
