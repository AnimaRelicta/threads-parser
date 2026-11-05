"""Проверка разбора threads_posts.txt, плана по БД и строки прогресса без сети и БД.

Запуск: uv run python tests/postlist_check.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from serp.postlist import (  # noqa: E402
    PostTask,
    Progress,
    extract_listed,
    merge_entries,
    parse_entries,
    plan_tasks,
)

# --- разбор файла -------------------------------------------------------------
lines = [
    "# комментарий",
    "",
    "https://www.threads.com/@olx_vetka/post/DAbc123_-x?xmt=AM&slof=1",
    "https://threads.net/@user.name/post/DZZZ999",
    "@other/post/DQQQ111",
    "https://www.threads.com/t/DSHORT1",
    "DBARE222",
    "https://www.threads.com/@olx_vetka/post/DAbc123_-x",  # повтор
    "https://www.threads.com/@olx_vetka",  # профиль, не ветка
    "не ссылка",
]
got = dict(parse_entries(lines))
assert got == {
    "DAbc123_-x": "olx_vetka",
    "DZZZ999": "user.name",
    "DQQQ111": "other",
    "DSHORT1": None,
    "DBARE222": None,
}, got
# ник из более полной записи не теряется при слиянии
assert dict(merge_entries([("A1", None)], [("A1", "u")]))["A1"] == "u"
assert dict(merge_entries([("A1", "u")], [("A1", None)]))["A1"] == "u"


# --- план по БД ---------------------------------------------------------------
class Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, codes):
        return [r for r in self.rows if r["code"] in codes]


class Pool:
    def __init__(self, rows):
        self.conn = Conn(rows)

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *a):
                return False

        return Ctx()


now = datetime.now(timezone.utc)
rows = [
    {"code": "OLD1", "author_username": "a", "updated_at": now - timedelta(days=10), "parsed": True},
    {"code": "NEW1", "author_username": "b", "updated_at": now - timedelta(hours=2), "parsed": False},
]
entries = [("OLD1", None), ("NEW1", "b"), ("FRESH", "c"), ("NOUSER", None)]

tasks, info = asyncio.run(plan_tasks(Pool(rows), entries, refresh=False, stale_days=0))
assert tasks == [PostTask("FRESH", "c", False)], tasks  # существующие пропущены, без ника нельзя
assert (info["new"], info["in_db"], info["no_user"], info["refresh"]) == (1, 2, 1, 0), info

tasks, info = asyncio.run(plan_tasks(Pool(rows), entries, refresh=True, stale_days=0))
assert {t.code for t in tasks} == {"FRESH", "OLD1", "NEW1"}, tasks
assert next(t for t in tasks if t.code == "OLD1") == PostTask("OLD1", "a", True)  # ник из БД

tasks, info = asyncio.run(plan_tasks(Pool(rows), entries, refresh=True, stale_days=3))
assert {t.code for t in tasks} == {"FRESH", "OLD1"}, tasks  # NEW1 менялась 2 часа назад
assert info["fresh"] == 1, info

# --- извлечение ---------------------------------------------------------------
media = {
    "user": {"username": "RealName", "pk": 42},
    "caption": {"text": "олх ветка #tag @x"},
    "taken_at": 1_700_000_000,
    "like_count": 5,
    "text_post_app_info": {"direct_reply_count": 3, "repost_count": 1, "quote_count": 2},
}
p = extract_listed(media, "fromurl", "CODE1")
assert p["username"] == "RealName" and p["user_id"] == "42" and p["url"].endswith("/@RealName/post/CODE1")
assert (p["likes"], p["reply_count"], p["reposts"], p["quotes"]) == (5, 3, 1, 2)
assert extract_listed({}, "fromurl", "C")["username"] == "fromurl"

# --- прогресс -----------------------------------------------------------------
pr = Progress(3)
assert pr.tick().startswith("1/3, осталось 2")
assert pr.tick().startswith("2/3, осталось 1")

print("postlist_check: ok")
