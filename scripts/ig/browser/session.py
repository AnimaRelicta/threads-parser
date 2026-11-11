"""Сессия аккаунта Instagram: куки, проверка входа, определение ника."""

import json
import os
import re
from pathlib import Path

from browser.session import load_cookies  # noqa: F401  (тот же формат файла кук)
from config import IG
from ig.consts import JS_FETCH


async def save_cookies(ctx, user_agent: str, cookies_file: Path) -> None:
    """Атомарная запись кук (пишет один раз аккаунт при закрытии браузера)."""
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"user_agent": user_agent, "cookies": await ctx.cookies(IG)}
    tmp = cookies_file.with_name(f"{cookies_file.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cookies_file)


async def has_session(ctx) -> bool:
    return any(c["name"] == "sessionid" and c["value"] for c in await ctx.cookies(IG))


async def session_user_id(ctx) -> str | None:
    return next((c["value"] for c in await ctx.cookies(IG) if c["name"] == "ds_user_id"), None)


def nick_near_id(html: str, uid: str, radius: int = 300) -> str | None:
    """Ник, ближайший (не дальше radius символов) к id аккаунта в данных страницы."""
    positions = [m.start() for m in re.finditer(re.escape(uid), html)]
    best: tuple[int, str] | None = None
    for m in re.finditer(r'"username":"([A-Za-z0-9._]{1,30})"', html):
        dist = min((abs(m.start() - q) for q in positions), default=None)
        if dist is not None and dist <= radius and (best is None or dist < best[0]):
            best = (dist, m.group(1))
    return best[1] if best else None


async def find_username(page) -> str | None:
    """Ник вошедшего аккаунта: API профиля по ds_user_id, затем данные страницы."""
    uid = await session_user_id(page.context)
    if uid:
        res = await page.evaluate(JS_FETCH, f"/api/v1/users/{uid}/info/")
        if isinstance(res, dict) and isinstance(res.get("user"), dict):
            name = res["user"].get("username")
            if name:
                return name
    try:
        html = await page.content()
    except Exception:
        return None
    return nick_near_id(html, uid) if uid else None


async def check_session(page) -> str | None:
    """Проверяет, что сессия живая: ник, "?" (живая, ник не определён) или None."""
    await page.goto(f"{IG}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)

    res = await page.evaluate(JS_FETCH, "/api/v1/accounts/current_user/?edit=true")
    alive = isinstance(res, dict) and isinstance(res.get("user"), dict)
    if alive and res["user"].get("username"):
        return res["user"]["username"]

    if not alive:  # запасная проверка: нет формы входа и редиректа на логин
        alive = (
            "/accounts/login" not in page.url
            and await page.query_selector('input[name="username"]') is None
            and await has_session(page.context)
        )
    if not alive:
        return None
    return await find_username(page) or "?"
