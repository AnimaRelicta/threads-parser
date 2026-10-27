"""Реестр аккаунтов в Postgres (таблица accounts) и синхронизация файлов кук с ним."""

import json
from datetime import datetime, timezone

from config import Settings
from db import accounts as repo
from db.pool import connection
from registry.cookies import cookie_jar


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def load_registry(st: Settings) -> dict[str, dict]:
    """{имя: запись} по всем аккаунтам."""
    async with connection(st) as conn:
        return await repo.fetch_accounts(conn)


async def get_account(st: Settings, name: str) -> dict | None:
    async with connection(st) as conn:
        return await repo.fetch_account(conn, name)


async def register_login(
    st: Settings, name: str, username: str | None, user_id: str | None
) -> None:
    """Заводит аккаунт или отмечает повторный вход (статус active, ошибка сброшена)."""
    async with connection(st) as conn:
        await repo.register_login(conn, name, username, user_id)


async def update_account(st: Settings, name: str, **fields) -> None:
    async with connection(st) as conn:
        await repo.update_account(conn, name, **fields)


async def sync_cookie_files(st: Settings) -> list[str]:
    """Заводит в реестре файлы кук, которые положили в папку руками (без import)."""
    if not st.cookies_dir.exists():
        return []
    reg = await load_registry(st)
    added = []
    for f in sorted(st.cookies_dir.glob("*.json")):
        if f.stem in reg:
            continue
        try:
            jar = cookie_jar(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if jar.get("sessionid"):
            await register_login(st, f.stem, None, jar.get("ds_user_id"))
            added.append(f.stem)
    if added:
        print(f"[+] Найдены файлы кук вне реестра, добавлены: {', '.join(added)}")
    return added


async def list_active(st: Settings) -> list[str]:
    """Имена аккаунтов, которыми можно парсить."""
    await sync_cookie_files(st)
    return [
        n
        for n, r in (await load_registry(st)).items()
        if r.get("status", "active") == "active" and st.cookies_path(n).exists()
    ]


async def pick_accounts(st: Settings, requested: list[str] | None) -> list[str]:
    """Аккаунты для запуска: указанные или все активные."""
    active = await list_active(st)
    if requested:
        reg = await load_registry(st)
        bad = [n for n in requested if n not in reg or not st.cookies_path(n).exists()]
        if bad:
            raise SystemExit(
                f"Нет аккаунтов: {', '.join(bad)}. Есть: {', '.join(active) or 'пусто'} "
                "(uv run scripts/accounts.py list)"
            )
        return list(dict.fromkeys(requested))
    if not active:
        raise SystemExit(
            "Нет активных аккаунтов. Войдите: uv run scripts/accounts.py add 1 "
            "или перенесите куки: uv run scripts/accounts.py import <файл>"
        )
    return active


def next_name(st: Settings, reg: dict, prefix: str = "account") -> str:
    i = 1
    while True:
        name = f"{prefix}_{i:03d}"
        if name not in reg and not st.cookies_path(name).exists():
            return name
        i += 1


def find_duplicate(reg: dict, user_id: str | None) -> str | None:
    for n, r in reg.items():
        if user_id and r.get("user_id") == user_id:
            return n
    return None
