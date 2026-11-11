"""Проверка шага 2 Instagram без БД и браузера: аккаунты, куки, пауза, перенос из insta-parser."""

import json
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import asyncpg  # noqa: F401
except ImportError:
    fake = types.ModuleType("asyncpg")
    for name in ("Connection", "Pool", "PostgresError", "InterfaceError"):
        setattr(fake, name, type(name, (Exception,), {}))
    sys.modules["asyncpg"] = fake

from config import COOKIE_DOMAIN, IG_COOKIE_DOMAIN, IgSettings  # noqa: E402
from ig.browser.session import nick_near_id  # noqa: E402
from ig.consts import JS_FETCH  # noqa: E402
from ig.db.accounts import EDITABLE, legacy_row  # noqa: E402
from ig.registry import cli as accounts_cli  # noqa: E402
from ig.registry import commands, login, store  # noqa: E402, F401
from registry.commands import collect_bundle  # noqa: E402
from registry.cookies import normalize_cookie  # noqa: E402

# --- куки: домен по умолчанию зависит от платформы, Threads не изменился
bare = {"name": "sessionid", "value": "abc"}
assert normalize_cookie(bare)["domain"] == COOKIE_DOMAIN == ".threads.com"
assert normalize_cookie(bare, IG_COOKIE_DOMAIN)["domain"] == ".instagram.com"
assert normalize_cookie({**bare, "domain": ".x.com"}, IG_COOKIE_DOMAIN)["domain"] == ".x.com"

# --- пауза после 429
now = datetime.now(timezone.utc)
ok = {"status": "active", "cooldown_until": None}
assert store.available(ok, now)
assert store.available({"status": "active", "cooldown_until": now - timedelta(seconds=1)}, now)
assert not store.available({"status": "active", "cooldown_until": now + timedelta(minutes=5)}, now)
assert not store.available({"status": "needs_login", "cooldown_until": None}, now)
assert not store.available({"status": "disabled"}, now)
assert "cooldown_until" in EDITABLE

# --- перенос реестра insta-parser: поля instagram_* -> username/user_id
legacy = {
    "instagram_username": "someone",
    "instagram_user_id": "123",
    "cookies_file": "account_001.json",
    "status": "needs_login",
    "created_at": "2026-10-15T09:17:05+00:00",
    "last_run_at": None,
    "stats": {"runs": 3},
}
r = legacy_row("account_001", legacy)
assert (r["username"], r["user_id"], r["status"]) == ("someone", "123", "needs_login")
assert r["created_at"].year == 2026 and r["last_run_at"] is None and "stats" not in r
assert legacy_row("a", {"status": "weird"})["status"] == "active"
assert legacy_row("a", {})["cookies_file"] == "a.json"

# --- архив с куками и accounts.json (общий разбор из threads)
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "bundle.zip"
    import zipfile

    with zipfile.ZipFile(p, "w") as z:
        z.writestr("cookies/account_001.json", json.dumps({"cookies": [bare], "user_agent": "ua"}))
        z.writestr("accounts.json", json.dumps({"account_001": {"username": "u"}}))
    items, reg = collect_bundle(p)
    assert [s for s, _ in items] == ["account_001"] and reg["account_001"]["username"] == "u"

# --- настройки и пути
st = IgSettings()
assert st.cookies_path("account_001").parent.name == "ig_cookies"
assert st.pause_429 == 300

# --- ник по id в данных страницы: берётся ближайший к id
html = '{"username":"far","x":1}' + "x" * 600 + '{"pk":"777","username":"near"}'
assert nick_near_id(html, "777") == "near"
assert nick_near_id('{"username":"far"}' + "x" * 600 + '"pk":"777"', "777") is None
assert "X-IG-App-ID" in JS_FETCH and "936619743392459" in JS_FETCH

# --- разбор аргументов
ap = accounts_cli.build_parser()
assert ap.parse_args(["add", "2"]).count == 2
assert ap.parse_args(["check", "--headless", "a"]).names == ["a"]
a = ap.parse_args(["import-legacy", "--file", "x.json", "--cookies-dir", "c"])
assert a.file == Path("x.json") and a.cookies_dir == Path("c")
assert ap.parse_args(["cooldown", "account_001", "--clear"]).clear is True

print("ig_step2_check: OK")
