#!/usr/bin/env python3
"""Управление базой Postgres: миграции и состояние.

Команды (из корня проекта):
    uv run scripts/dbadmin.py migrate     # создать схему threads и применить миграции
    uv run scripts/dbadmin.py status      # применённые миграции и число строк в таблицах
    uv run scripts/dbadmin.py fix-encoding        # починить био и др. с «\\u041f\\u043e...» (--dry-run)
    uv run scripts/dbadmin.py import-accounts   # перенести аккаунты из data/accounts.json
    uv run scripts/dbadmin.py import-profiles   # перенести posts.json, anchor.json и др. профилей
    uv run scripts/dbadmin.py import-search     # перенести posts.jsonl и jobs.json поиска
    uv run scripts/dbadmin.py merge-old-profiles  # дополнить threads.profiles из старой БД threads_old (--dry-run)
    uv run scripts/cli.py db migrate      # то же через единую точку входа

Строка подключения берётся из DATABASE_URL в scripts/.env.
"""

import argparse
import json
from pathlib import Path

from config import load_settings
from db.accounts import import_legacy
from db.geo import fix_encoding
from db.legacy import import_profiles, import_search
from db.merge_old import connect_old, merge_old_profiles, old_url_from
from db.migrate import migrate, status
from db.pool import require_url, run_sync


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


def cmd_fix_encoding(st, dry_run: bool) -> None:
    """Текст профилей с неразобранными \\uXXXX (био, имя, заголовок) -> нормальный текст."""
    n, per_col = run_sync(st, lambda conn: fix_encoding(conn, not dry_run))
    cols = ", ".join(f"{c}: {k}" for c, k in per_col.items() if k)
    verb = "нужно исправить" if dry_run else "исправлено"
    print(f"[+] Профилей, где {verb}: {n}" + (f" ({cols})" if cols else ""))
    if dry_run and n:
        print("[*] Без --dry-run: uv run scripts/dbadmin.py fix-encoding")


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


def cmd_import_profiles(st, root: Path | None, only: list[str] | None) -> None:
    """Разовый перенос профилей из data/output/<ник>/: посты, комментарии, медиа, якорь."""
    root = root or st.output_dir
    if not root.is_dir():
        raise SystemExit(f"Папка не найдена: {root}")
    res = run_sync(st, lambda conn: import_profiles(conn, root, only))
    if not res:
        print("[*] Папок профилей с posts.json не найдено")
        return
    for name, c in res.items():
        print(
            f"  @{name}: постов {c['posts']} (уже в БД {c['kept']}), комментариев {c['comments']}, "
            f"пропущенных {c['skipped']}, якорь {'да' if c['anchor'] else 'нет'}"
        )
    total = sum(c["posts"] for c in res.values())
    print(f"[+] Профилей обработано: {len(res)}, постов перенесено: {total}")


def cmd_import_search(st, root: Path | None) -> None:
    """Разовый перенос результатов поиска из SEARCH_OUTPUT_DIR (posts.jsonl, jobs.json)."""
    root = root or st.search_output_dir
    posts_path, jobs_path = root / "posts.jsonl", root / "jobs.json"
    if not posts_path.exists() and not jobs_path.exists():
        raise SystemExit(f"В {root} нет ни posts.jsonl, ни jobs.json")
    r = run_sync(st, lambda conn: import_search(conn, posts_path, jobs_path))
    print(f"[+] Задач перенесено: {r['jobs']} (уже были: {r['jobs_kept']})")
    print(f"[+] Постов перенесено: {r['posts']} (уже были в БД: {r['posts_kept']})")


def cmd_merge_old_profiles(st, args) -> None:
    """Дополняет пустые поля threads.profiles данными threads_old.profiles (по нику)."""
    old_url = old_url_from(require_url(st), args.old_url)

    async def _run(conn):
        old = await connect_old(old_url)
        try:
            return await merge_old_profiles(
                conn, old, args.columns, args.exclude, args.overwrite, args.dry_run
            )
        finally:
            await old.close()

    r = run_sync(st, _run)
    print(f"[*] Профилей в обеих схемах: {r['matched']}; только в threads_old: {r['only_old']}")
    for col, why in r["skipped"].items():
        print(f"    пропущена колонка {col}: {why}")
    if not r["filled"]:
        print("[*] Заполнять нечего")
        return
    print("[*] Значений " + ("будет внесено" if args.dry_run else "внесено") + " по колонкам:")
    for col, n in sorted(r["filled"].items(), key=lambda kv: -kv[1]):
        print(f"    {col}: {n}")
    if args.dry_run:
        print("[*] --dry-run: ничего не записано")
    else:
        print(f"[+] Обновлено профилей: {r['updated']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Postgres: миграции и состояние")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="применить недостающие миграции")
    sub.add_parser("status", help="показать применённые миграции и размеры таблиц")
    imp = sub.add_parser("import-accounts", help="перенести аккаунты из accounts.json в БД")
    imp.add_argument("--file", type=Path, help="по умолчанию ACCOUNTS_FILE из .env")
    fx = sub.add_parser("fix-encoding", help="починить текст с \\uXXXX (био, имя, заголовок)")
    fx.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не писать")
    ip = sub.add_parser("import-profiles", help="перенести данные профилей из data/output в БД")
    ip.add_argument("--dir", type=Path, help="по умолчанию OUTPUT_DIR из .env")
    ip.add_argument("profiles", nargs="*", help="только эти профили (по умолчанию все)")
    isr = sub.add_parser("import-search", help="перенести posts.jsonl и jobs.json поиска в БД")
    isr.add_argument("--dir", type=Path, help="по умолчанию SEARCH_OUTPUT_DIR из .env")
    mo = sub.add_parser(
        "merge-old-profiles", help="дополнить threads.profiles данными из старой БД threads_old"
    )
    mo.add_argument(
        "--old-url",
        help="адрес старой БД (по умолчанию DATABASE_URL с именем базы threads_old)",
    )
    mo.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не писать")
    mo.add_argument("--columns", nargs="+", help="только эти колонки (по умолчанию все общие)")
    mo.add_argument("--exclude", nargs="+", help="не трогать эти колонки")
    mo.add_argument(
        "--overwrite",
        action="store_true",
        help="перезаписывать и заполненные значения (по умолчанию только пустые)",
    )
    args = ap.parse_args()
    st = load_settings()
    try:
        if args.cmd == "merge-old-profiles":
            cmd_merge_old_profiles(st, args)
        elif args.cmd == "import-search":
            cmd_import_search(st, args.dir)
        elif args.cmd == "import-profiles":
            cmd_import_profiles(st, args.dir, args.profiles or None)
        elif args.cmd == "fix-encoding":
            cmd_fix_encoding(st, args.dry_run)
        elif args.cmd == "import-accounts":
            cmd_import_accounts(st, args.file)
        else:
            {"migrate": cmd_migrate, "status": cmd_status}[args.cmd](st)
    except KeyboardInterrupt:
        print("\n[!] Остановлено пользователем.")


if __name__ == "__main__":
    main()
