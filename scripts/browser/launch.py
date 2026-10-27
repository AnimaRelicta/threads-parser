"""Запуск Chromium: прокси и контекст."""

from config import Settings
from registry.store import load_registry


def proxy_settings(proxy: str | None) -> dict | None:
    """'scheme://[user:pass@]host:port' -> словарь proxy для Playwright (None = без прокси)."""
    if not proxy:
        return None
    from urllib.parse import unquote, urlsplit

    u = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    if not u.hostname:
        raise SystemExit(f"Неверный адрес прокси «{proxy}»")
    server = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    out = {"server": server}
    if u.username:
        out["username"] = unquote(u.username)
        out["password"] = unquote(u.password or "")
    return out


def account_proxy(st: Settings, name: str) -> str | None:
    """Прокси аккаунта: поле proxy в реестре > общий PROXY > без прокси."""
    rec = load_registry(st).get(name) or {}
    return (rec.get("proxy") or "").strip() or st.proxy


async def launch_browser(p, headless: bool, proxy: str | None = None):
    """Запускает браузер: пробуем Chrome, Edge, затем встроенный Chromium.
    proxy: см. proxy_settings (на него идёт и трафик ctx.request, т. е. скачивание медиа)."""
    last = None
    px = proxy_settings(proxy)
    for channel in ("chrome", "msedge", None):
        kwargs = {"headless": headless}
        if px:
            kwargs["proxy"] = px
        if channel:
            kwargs["channel"] = channel
        try:
            return await p.chromium.launch(**kwargs)
        except Exception as e:
            last = e
    raise SystemExit(
        f"Не удалось открыть браузер ({last}).\nВыполните: uv run playwright install chromium"
    )


async def new_context(browser, data: dict | None = None):
    """Новый контекст; при наличии data подставляет User-Agent и куки."""
    kwargs = {"viewport": {"width": 1280, "height": 900}}
    if data:
        kwargs["user_agent"] = data["user_agent"]
    ctx = await browser.new_context(**kwargs)
    ctx.set_default_timeout(30000)
    if data:
        await ctx.add_cookies(data["cookies"])
    return ctx
