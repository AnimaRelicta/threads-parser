"""Реестр аккаунтов Instagram в Postgres (схема instagram) и синхронизация файлов кук."""

import json
from datetime import datetime, timezone

from config import IgSettings
from db.pool import connection
from ig.consts import IG_SCHEMA
from ig.db import accounts as repo
from registry.cookies import cookie_jar
from registry.store import find_duplicate, next_name, now_iso  # noqa: F401  (общая логика имён)


def available(rec: dict, now: datetime | None = None) -> bool:
    """Можно ли брать аккаунт в запуск: активен и не на паузе после 429."""
    now = now or datetime.now(timezone.utc)
    cd = rec.get("cooldown_until")
    return rec.get("status", "active") == "active" and not (cd and cd > now)


async def load_registry(st: IgSettings) -> dict[str, dict]:
    async with connection(st, IG_SCHEMA) as conn:
        return await repo.fetch_accounts(conn)


async def get_account(st: IgSettings, name: str) -> dict | None:
    async with connection(st, IG_SCHEMA) as conn:
        return await repo.fetch_account(conn, name)


async def register_login(
    st: IgSettings, name: str, username: str | None, user_id: str | None
) -> None:
    async with connection(st, IG_SCHEMA) as conn:
        await repo.register_login(conn, name, username, user_id)


async def update_account(st: IgSettings, name: str, **fields) -> None:
    async with connection(st, IG_SCHEMA) as conn:
        await repo.update_account(conn, name, **fields)


async def set_cooldown(st: IgSettings, name: str, seconds: int | None = None) -> None:
    """Пауза аккаунта после 429 (по умолчанию IG_429_PAUSE); 0 снимает."""
    async with connection(st, IG_SCHEMA) as conn:
        await repo.set_cooldown(conn, name, st.pause_429 if seconds is None else seconds)


async def sync_cookie_files(st: IgSettings) -> list[str]:
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


async def pick_accounts(st: IgSettings, requested: list[str] | None) -> list[str]:
    """Аккаунты для запуска: указанные или все активные; аккаунты на паузе пропускаются."""
    await sync_cookie_files(st)
    reg = await load_registry(st)
    have_cookies = {n for n in reg if st.cookies_path(n).exists()}
    if requested:
        bad = [n for n in requested if n not in have_cookies]
        if bad:
            raise SystemExit(
                f"Нет аккаунтов Instagram: {', '.join(bad)}. "
                f"Есть: {', '.join(sorted(have_cookies)) or 'пусто'} (cli.py ig accounts list)"
            )
        names = list(dict.fromkeys(requested))
    else:
        names = sorted(have_cookies)
    now = datetime.now(timezone.utc)
    ready = [n for n in names if available(reg[n], now)]
    for n in names:
        if n not in ready:
            cd = reg[n].get("cooldown_until")
            why = f"пауза до {cd:%H:%M:%S} UTC" if cd and cd > now else f"статус {reg[n]['status']}"
            print(f"[*] Аккаунт {n} пропущен: {why}")
    if not ready:
        raise SystemExit(
            "Нет доступных аккаунтов Instagram. Войдите: cli.py ig accounts add 1 "
            "или перенесите: cli.py ig accounts import <файл>"
        )
    return ready
