"""Куки аккаунтов: разбор и нормализация чужих форматов."""

from config import COOKIE_DOMAIN

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def cookie_jar(data) -> dict:
    """{имя: значение} из файла кук (формат проекта или просто список кук)."""
    cookies = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(cookies, list):
        return {}
    return {c.get("name"): c.get("value") for c in cookies if isinstance(c, dict)}


def normalize_cookie(c: dict, domain: str = COOKIE_DOMAIN) -> dict | None:
    """Приводит куку к формату Playwright (понимает выгрузки Cookie-Editor и т.п.).

    domain: домен для кук, где он не указан (Threads по умолчанию, Instagram: .instagram.com)."""
    if not isinstance(c, dict) or not c.get("name") or c.get("value") is None:
        return None
    out = {
        "name": str(c["name"]),
        "value": str(c["value"]),
        "domain": c.get("domain") or domain,
        "path": c.get("path") or "/",
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": bool(c.get("secure", True)),
    }
    exp = c.get("expires", c.get("expirationDate"))
    out["expires"] = float(exp) if isinstance(exp, (int, float)) and exp > 0 else -1
    if c.get("session"):
        out["expires"] = -1
    same = SAMESITE.get(str(c.get("sameSite", "")).lower())
    if same:
        out["sameSite"] = same
    return out
