"""Проверка шага 1 Instagram без БД и сети: настройки, схема, миграция, параметры пула."""

import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# asyncpg нужен только для типов: подменяем, если не установлен
try:
    import asyncpg  # noqa: F401
except ImportError:
    fake = types.ModuleType("asyncpg")
    for name in ("Connection", "Pool", "PostgresError", "InterfaceError"):
        setattr(fake, name, type(name, (Exception,), {}))
    sys.modules["asyncpg"] = fake

from config import IgSettings, Settings, load_ig_settings  # noqa: E402
from db import migrate as mg  # noqa: E402
from db.pool import SCHEMA, _server_settings  # noqa: E402
from ig.consts import IG_LOCK_ID, IG_MIGRATIONS_DIR, IG_SCHEMA  # noqa: E402

# --- пул: схема threads по умолчанию, instagram по параметру
assert SCHEMA == "threads" and IG_SCHEMA == "instagram"
assert _server_settings() == {"search_path": "threads,public", "application_name": "threads-parser"}
assert _server_settings("instagram")["search_path"] == "instagram,public"

# --- миграции: прежние значения для threads, имя схемы проверяется
assert mg.MIGRATIONS_DIR.name == "migrations" and mg.LOCK_ID != IG_LOCK_ID
assert "threads.schema_migrations" in mg.bootstrap_sql("threads")
assert "instagram.schema_migrations" in mg.bootstrap_sql("instagram")
for bad in ("x; DROP TABLE a", "Threads", "1abc", ""):
    try:
        mg.bootstrap_sql(bad)
    except ValueError:
        continue
    raise AssertionError(bad)
names = [v for v, _ in mg.list_migrations()]
assert names[0] == "001_init" and names == sorted(names), names
ig_names = [v for v, _ in mg.list_migrations(IG_MIGRATIONS_DIR)]
assert ig_names == ["001_init"], ig_names

# --- SQL миграции instagram: всё внутри схемы instagram, нужные таблицы и CHECK
sql = (IG_MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8")
code = re.sub(r"'[^']*'", "''", re.sub(r"--.*", "", sql))  # без комментариев и строк
assert "threads." not in code, "миграция не должна трогать схему threads"
tables = set(re.findall(r"CREATE TABLE (\w+)", sql))
need = {
    "accounts",
    "runs",
    "run_accounts",
    "profiles",
    "profile_anchors",
    "profile_runs",
    "posts",
    "comments",
    "media",
}
assert tables == need, tables ^ need
assert "CHECK (source IN ('file','threads','cli'))" in sql
assert "CHECK (depth BETWEEN 1 AND 2)" in sql or "depth BETWEEN 1 AND 2" in sql
assert "profiles_username_lc ON profiles (lower(username))" in sql
assert sql.count("(") == sql.count(")"), "скобки в SQL не сходятся"

# --- настройки: значения по умолчанию и чтение переменных окружения
st = IgSettings()
assert (st.tabs, st.depth, st.images, st.videos, st.comments) == (3, 2, True, False, False)
assert st.storage_dir.name == "instagram-parcer" and st.storage_dir.parent.name == "storage"
assert st.storage_dir.parent.parent == ROOT.parent
assert st.cookies_path("account_001") == st.cookies_dir / "account_001.json"
assert st.cookies_dir != Settings().cookies_dir, "куки IG и Threads не должны смешиваться"

env_file = ROOT / "scripts" / ".env"
had_env = env_file.exists()
os.environ.update({"IG_TABS_PER_ACCOUNT": "5", "IG_VIDEOS": "true", "IG_STORAGE_DIR": "/data/ig"})
try:
    # переменные окружения применяются, только если ключ есть в .env: проверяем разбор файла
    from config import parse_env_file  # noqa: E402

    example = parse_env_file(ROOT / "scripts" / ".env.example")
    ig_keys = {k for k in example if k.startswith("IG_")}
    assert {"IG_TABS_PER_ACCOUNT", "IG_STORAGE_DIR", "IG_DEPTH", "IG_VIDEOS"} <= ig_keys
    cur = load_ig_settings()
    assert isinstance(cur, IgSettings)
finally:
    for k in ("IG_TABS_PER_ACCOUNT", "IG_VIDEOS", "IG_STORAGE_DIR"):
        os.environ.pop(k, None)
assert had_env == env_file.exists()

print("ig_step1_check: OK")
