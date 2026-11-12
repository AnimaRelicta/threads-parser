"""Открытие профиля Instagram: проверки доступа (вход, checkpoint, 404, 429)."""

from playwright.async_api import Error as PlaywrightError

from config import IG
from ig.consts import HEADER_WAIT_MS, PROFILE_SETTLE_MS
from ig.extract import header_text


def fail_auth(note: str) -> dict:
    return {"status": "abort", "abort_kind": "auth", "note": note}


def fail_rate() -> dict:
    return {"status": "abort", "abort_kind": "429", "note": "429 Too Many Requests"}


async def page_text(page, limit: int = 500) -> str:
    """Видимый текст страницы без подвала (для причины «профиль недоступен»)."""
    try:
        body = await page.evaluate("() => (document.body?.innerText || '').slice(0, 4000)")
    except PlaywrightError:
        return ""
    return header_text(body, limit) if isinstance(body, str) else ""


async def open_profile(page, username: str, account: str) -> dict | None:
    """Открывает страницу профиля. None — можно читать данные, иначе поля для result.

    Редирект на логин или checkpoint — проблема аккаунта (abort/auth); 429 — abort/429;
    404 — профиль не найден. Страница нужна и как origin для запросов API (JS_FETCH)."""
    resp = await page.goto(f"{IG}/{username}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(PROFILE_SETTLE_MS)
    if "/accounts/login" in page.url:
        return fail_auth(f"сессия недействительна: cli.py ig accounts relogin {account}")
    if "/challenge" in page.url or "/suspended" in page.url:
        return fail_auth(f"checkpoint/блокировка: войдите вручную (ig accounts relogin {account})")
    status = resp.status if resp is not None else None
    if status == 429:
        return fail_rate()
    if status == 404:
        text = await page_text(page)
        return {
            "status": "not_found",
            "note": "профиль не найден",
            "reason": {"source": "http", "raw": {"text": text or "HTTP 404"}},
        }
    try:  # шапку рисует React уже после domcontentloaded: без ожидания видны только og:*
        await page.wait_for_selector("header section", timeout=HEADER_WAIT_MS)
    except PlaywrightError:
        pass  # нет шапки (недоступный профиль и т. п.): дальше решают проверки текста
    return None
