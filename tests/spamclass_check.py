"""Проверка классов спам-профилей без сети и БД (core/spamclass.py).

Запуск: uv run python tests/spamclass_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.spamclass import (  # noqa: E402
    BOARD_AUTHOR,
    CLASS_RULES,
    SELLER,
    SPAMMER,
    class_rules_with,
    classify,
)

H = 3600
NOW = 1_800_000_000


def threads(gap_h: float, replies: list, n: int = 10) -> list[dict]:
    """n веток с паузой gap_h часов; replies по кругу (None = счётчик неизвестен)."""
    return [
        {"ts": NOW - int(i * gap_h * H), "replies": replies[i % len(replies)]} for i in range(n)
    ]


# 1. Редкие ветки (раз в 2 дня), много комментариев -> барахолка
v = classify(threads(48, [120, 80, 300]), followers=5000)
assert v.code == BOARD_AUTHOR and v.name == "board_author", v

# 2. Частые ветки (каждые 2 часа), мало комментариев -> продавец; подписчиков много
v = classify(threads(2, [0, 1, 2]), followers=5000)
assert v.code == SELLER, v

# 3. То же, подписчиков меньше порога -> спамер
v = classify(threads(2, [0, 1, 2]), followers=40)
assert v.code == SPAMMER, v
assert v.metrics["followers"] == 40

# 3'. Подписчиков ровно порог: уже не класс 3
assert classify(threads(2, [0]), followers=100).code == SELLER
assert classify(threads(2, [0]), followers=99).code == SPAMMER

# Подписчики неизвестны: класс 3 не назначаем
assert classify(threads(2, [0, 1]), followers=None).code == SELLER

# Частые + много комментариев, редкие + мало комментариев, середина: класса нет
assert classify(threads(2, [200]), followers=10).code is None
assert classify(threads(72, [0, 1]), followers=10).code is None
assert classify(threads(2, [10]), followers=10).code is None

# Берутся только 10 самых свежих: старый «хвост» с другими счётчиками не влияет
fresh = threads(2, [0], 10)
old = [{"ts": NOW - 400 * H - i * H, "replies": 500} for i in range(30)]
assert classify(fresh + old, followers=10).code == SPAMMER
assert classify(fresh + old, followers=10).sample == 10

# Закреплённая старая ветка в начале ленты (порядок items не важен)
pinned = {"ts": NOW - 900 * H, "replies": 999}
assert classify([pinned] + threads(2, [1]), followers=10).code == SPAMMER

# Мало данных: класса нет и no_data (прежний класс в БД трогать нельзя)
v = classify(threads(2, [0], n=4), followers=10)
assert v.code is None and v.no_data, v
v = classify(threads(2, [None]), followers=10)
assert v.code is None and v.no_data, v
v = classify([], followers=10)
assert v.code is None and v.no_data

# Часть счётчиков неизвестна: медиана по известным
v = classify(threads(2, [0, None]), followers=10)
assert v.code == SPAMMER and v.metrics["replies_known"] == 5

# Пороги из настроек
r = class_rules_with(sample=6, low_followers=1000, frequent_gap_hours=1)
assert (r.sample, r.low_followers, r.frequent_gap_sec, r.min_threads) == (6, 1000, 3600, 5)
assert classify(threads(2, [300]), followers=10, rules=r).code == BOARD_AUTHOR
assert class_rules_with(sample=3).min_threads == 3
assert CLASS_RULES.sample == 10 and CLASS_RULES.low_followers == 100

print("spamclass_check: OK")
