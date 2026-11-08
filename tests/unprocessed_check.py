"""Проверка --only-unprocessed и ожидания профиля без сети и БД.

Запуск: uv run python tests/unprocessed_check.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from playwright.async_api import Error as PlaywrightError  # noqa: E402

from db.profiles import DONE_STATUSES, LEVEL_OF, drop_processed, levels_covering  # noqa: E402
from scrape.page import wait_profile_ready  # noqa: E402

# какие отметки покрывают режим запуска
assert levels_covering("profile") == ["profile", "threads", "full"]
assert levels_covering("threads") == ["threads", "full"]
assert levels_covering("full") == ["full"]
assert levels_covering("fill") == []  # fill сам берёт только неразобранные ветки
assert LEVEL_OF["only_new"] == "full" and LEVEL_OF["fill"] == "full"
# сбои обработкой не считаются
assert (
    "abort" not in DONE_STATUSES and "error" not in DONE_STATUSES and "not_run" not in DONE_STATUSES
)


class Conn:
    """processed: ник(нижний регистр) -> processed_mode."""

    def __init__(self, processed):
        self.processed, self.calls = processed, 0

    async def fetch(self, _sql, names, levels):
        self.calls += 1
        return [
            {"u": n}
            for n in names
            if self.processed.get(n) in levels
        ]


db = {"a": "profile", "b": "threads", "c": "full", "d": None}
names = ["A", "B", "C", "D", "E"]
assert asyncio.run(drop_processed(Conn(db), names, "profile")) == ["D", "E"]
assert asyncio.run(drop_processed(Conn(db), names, "threads")) == ["A", "D", "E"]
assert asyncio.run(drop_processed(Conn(db), names, "full")) == ["A", "B", "D", "E"]
c = Conn(db)
assert asyncio.run(drop_processed(c, names, "fill")) == names and c.calls == 0  # без запроса



class Page:
    def __init__(self, found):
        self.found, self.waited = found, []

    async def wait_for_selector(self, sel, state, timeout):
        assert "/post/" in sel and state == "attached"
        if not self.found:
            raise PlaywrightError("Timeout")

    async def wait_for_timeout(self, ms):
        self.waited.append(ms)


p = Page(True)
asyncio.run(wait_profile_ready(p))
assert p.waited == [600]  # дождались ветки: короткая пауза на шапку
p = Page(False)
asyncio.run(wait_profile_ready(p))
assert p.waited == []  # веток нет: ждал сам wait_for_selector до потолка, паузы нет

print("unprocessed_check: OK")
