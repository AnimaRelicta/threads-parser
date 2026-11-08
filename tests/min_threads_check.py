"""Проверка --min-threads без браузера и БД: подмена ленты и хранилища.

Запуск: uv run python tests/min_threads_check.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from scrape import threads  # noqa: E402


class Feed:
    def __init__(self, n):
        # ленты: t0 свежее всех; ts убывает
        self.items = {f"c{i}": {"ts": 2_000_000_000 - i * 1000, "text": "x"} for i in range(n)}

    def update(self, _):
        pass


class Page:
    async def wait_for_timeout(self, _):
        pass


class Store:
    def __init__(self, known):
        self.known, self.saved = set(known), []

    async def ensure_profile(self, _):
        return 1

    async def known_codes(self, _):
        return set(self.known)

    async def save_threads_light(self, pid, username, items):
        self.saved += [i["code"] for i in items]


def run(known, feed_len, **kw):
    store, feed, opened = Store(known), Feed(feed_len), []

    async def fake_open(*a, **k):
        opened.append(1)
        return feed, None

    async def fake_links(page, with_videos, username):
        for c in feed.items:
            yield c, "post"

    threads.open_profile, threads.iter_post_links = fake_open, fake_links
    args = SimpleNamespace(cutoff=None, full=False, stop_after=1, limit=kw.get("limit", 0),
                           min_threads=kw.get("min_threads", 0))  # fmt: skip
    net = SimpleNamespace(grid_captured=[])
    res = asyncio.run(threads.run_threads(Page(), net, "u", args, store))
    return res, store.saved, opened


# 1. В БД уже 50: профиль не открывается
res, saved, opened = run({f"c{i}" for i in range(50)}, 100, min_threads=50)
assert not opened and not saved and res["stop_reason"] == "enough" and res["status"] == "ok", res

# 2. Пусто: собирает ровно до 50
res, saved, opened = run(set(), 120, min_threads=50)
assert opened and len(saved) == 50 and res["stop_reason"] == "limit", (len(saved), res)

# 3. В БД 20 самых свежих: известные не останавливают обход, добирается ещё 30 новых
res, saved, _ = run({f"c{i}" for i in range(20)}, 120, min_threads=50)
assert len(set(saved) - {f"c{i}" for i in range(20)}) == 30 and res["saved"] == 30, res

# 4. Лента короче нужного: идёт до конца
res, saved, _ = run(set(), 12, min_threads=50)
assert len(saved) == 12 and res["stop_reason"] == "end", res

# 5. Без флага прежнее поведение: первая известная ветка останавливает обход
res, saved, _ = run({"c0"}, 120)
assert res["stop_reason"] == "known", res

print("min_threads_check: OK")
