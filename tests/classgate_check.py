"""Проверка отбора по классу (`--only-class`) без сети и БД (scrape/classgate.py).

Запуск: uv run python tests/classgate_check.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.spamclass import CLASS_RULES, parse_classes  # noqa: E402
from scrape import classgate  # noqa: E402
from scrape.classgate import Gate, gate_db, precheck, skip  # noqa: E402

# parse_classes
assert parse_classes("1,3") == {1, 3}
assert parse_classes("Seller, spammer") == {2, 3}
for bad in ("", "4", "x", ","):
    try:
        parse_classes(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"«{bad}» должно отвергаться")


class Verdict:
    def __init__(self, code, no_data=False, sample=10):
        self.code, self.no_data, self.sample, self.reason = code, no_data, sample, "причина"


class Store:
    """Хранилище-заглушка: state = (класс, время расчёта), detect = что вернёт детекция."""

    class_rules = CLASS_RULES

    def __init__(self, state, detect=None):
        self.state, self.detect, self.detected = state, detect, 0

    async def class_state(self, _pid):
        return self.state

    async def detect_class_db(self, _pid):
        self.detected += 1
        return self.detect


def run(coro):
    return asyncio.run(coro)


W = frozenset({1, 2})
# уже считали: решение из БД, детекция не запускается
s = Store((1, "t"))
g = run(precheck(s, 1, W))
assert g and g.keep
assert not run(precheck(Store((3, "t")), 1, W)).keep
assert not run(precheck(Store((None, "t")), 1, W)).keep  # считали, класса нет: не наш
assert run(precheck(Store((None, None)), 1, W)) is None  # не считали: нужна детекция

# gate_db: не считали -> детекция по веткам из БД
s = Store((None, None), Verdict(2))
assert run(gate_db(s, 1, "u", W)).keep and s.detected == 1
s = Store((None, None), Verdict(3))
assert not run(gate_db(s, 1, "u", W)).keep
s = Store((None, None), Verdict(None, no_data=True, sample=2))
g = run(gate_db(s, 1, "u", W))
assert not g.keep and "не определён" in g.note
s = Store((2, "t"), Verdict(3))  # класс уже есть: детекция не нужна
assert run(gate_db(s, 1, "u", W)).keep and s.detected == 0

# skip: статус и причина в результате
r = skip({"username": "u"}, Gate(False, "причина"), "u")
assert (r["status"], r["stop_reason"], r["note"]) == ("skipped", "class", "причина")
assert r["counts"] == {} and classgate.CLASS_NAMES[1] == "board_author"

print("classgate_check: OK")
