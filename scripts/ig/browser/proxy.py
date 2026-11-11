"""Прокси аккаунта Instagram: колонка instagram.accounts.proxy > общий PROXY > без прокси."""

from config import IgSettings
from ig.registry.store import get_account


async def account_proxy(st: IgSettings, name: str) -> str | None:
    rec = await get_account(st, name) or {}
    return (rec.get("proxy") or "").strip() or st.proxy
