"""Сессия аккаунта: куки, проверка входа, определение имени пользователя."""

import json
import os
import re
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from config import TH, TH_URLS
from core.consts import USERNAME_RE
from core.jsonwalk import walk


def load_cookies(cookies_file: Path) -> dict | None:
    """Читает файл кук (формат: {"user_agent": ..., "cookies": [...]})."""
    if not cookies_file.exists():
        return None
    try:
        data = json.loads(cookies_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("cookies") and data.get("user_agent"):
        return data
    return None


async def save_cookies(ctx, user_agent: str, cookies_file: Path) -> None:
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"user_agent": user_agent, "cookies": await ctx.cookies(TH_URLS)}
    tmp = cookies_file.with_name(f"{cookies_file.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, cookies_file)  # атомарно; пишет один раз аккаунт при закрытии


async def has_session(ctx) -> bool:
    cookies = await ctx.cookies(TH_URLS)
    return any(c["name"] == "sessionid" and c["value"] for c in cookies)


async def check_session(page) -> str | None:
    """Проверяет, что сессия живая. Возвращает метку или None."""
    await page.goto(f"{TH}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    if "/login" in page.url:
        return None
    if await has_session(page.context):
        return "ok"
    return None


# Ключи, под которыми Threads кладёт id пользователя в свои JSON
USER_ID_KEYS = ("pk", "id", "pk_id", "strong_id__")


NAV_PROFILE_JS = r"""() => {
  const h = window.innerHeight;
  for (const a of document.querySelectorAll('a[href^="/@"]')) {
    const href = a.getAttribute('href') || '';
    if (!/^\/@[^\/?#]+\/?$/.test(href)) continue;
    const r = a.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right < 140 || r.top > h - 140) return href;   // боковая или нижняя панель
  }
  return null;
}"""


async def session_user_id(ctx) -> str | None:
    cookies = await ctx.cookies(TH_URLS)
    return next((c["value"] for c in cookies if c["name"] == "ds_user_id"), None)


async def detect_username(page, user_id: str | None) -> str | None:
    """Ник залогиненного аккаунта по открытой главной странице Threads.

    1) в встроенном JSON ищем объект пользователя с этим id и полем username;
    2) если не вышло, берём ссылку на профиль из боковой/нижней панели навигации.
    Не нашли: None (ник остаётся пустым, на работу парсера это не влияет).
    """
    if user_id:
        try:
            scripts = await page.eval_on_selector_all(
                'script[type="application/json"]', "els => els.map(e => e.textContent)"
            )
        except PlaywrightError:
            scripts = []
        for txt in scripts:
            if not txt or user_id not in txt or '"username"' not in txt:
                continue
            try:
                doc = json.loads(txt)
            except (json.JSONDecodeError, TypeError):
                continue
            for d in walk(doc):
                u = d.get("username")
                if (
                    isinstance(u, str)
                    and USERNAME_RE.match(u)
                    and any(str(d.get(k)) == user_id for k in USER_ID_KEYS)
                ):
                    return u
    try:
        href = await page.evaluate(NAV_PROFILE_JS)
    except PlaywrightError:
        return None
    m = re.match(r"^/@([A-Za-z0-9._]+)/?$", href or "")
    return m.group(1) if m else None
