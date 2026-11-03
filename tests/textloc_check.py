"""Проверка починки кодировки био, числа веток и локации из текста без сети и БД.

Запуск: uv run python tests/textloc_check.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.biography import biography_from_json  # noqa: E402
from core.followers import threads_from_og  # noqa: E402
from core.geo import (  # noqa: E402
    DEFAULT_TEXT_SOURCES,
    clean_description,
    clean_title,
    parse_text_sources,
    text_location,
)
from core.textfix import decode_json_body, fix_text, has_json_escapes  # noqa: E402
from exporter.queries import BASE_QUERIES  # noqa: E402

# 1. Кодировка: запись из БД «как есть» превращается в текст
broken = (
    "\\u041f\\u043e\\u043a\\u0443\\u043f\\u043a\\u0430 | \\u043f\\u0440\\u043e\\u0434\\u0430\\u0436\\u0430"
    "\\n\\ud83d\\udcd1\\u042e\\u0440\\u0438\\u0434\\u0438\\u0447\\u0435\\u0441\\u043a\\u043e\\u0435"
    "\\n\\u0422\\u0435\\u043b\\u0435\\u0444\\u043e\\u043d \\ud83d\\udcf1 8 776 823 79 02"
)
assert has_json_escapes(broken)
fixed = fix_text(broken)
assert fixed == "Покупка | продажа\n📑Юридическое\nТелефон 📱 8 776 823 79 02", fixed
assert fix_text(fixed) == fixed and not has_json_escapes(fixed)  # повтор безопасен
assert fix_text("обычный текст") == "обычный текст" and fix_text(None) is None
assert decode_json_body('a \\"b\\" \\/ c') == 'a "b" / c'
assert decode_json_body("\\ud83d") == "\ufffd"  # одиночная половина пары не роняет запись
assert decode_json_body('без "кавычек" \\u0041') == 'без "кавычек" A'  # битый JSON: хотя бы \u

# 2. Био из JSON страницы: и экранированная кириллица, и обычная
page = '{"username":"u1","biography":"\\u041f\\u0440\\u0438\\u0432\\u0435\\u0442 \\ud83d\\udcd1","x":1}'
assert biography_from_json([page], "u1") == "Привет 📑"
assert biography_from_json(['{"username":"u1","biography":"Алматы"}'], "u1") == "Алматы"

# 3. Число веток
og = "353 подписчика • 169 веток • Покупка | продажа. Посмотреть последние..."
assert threads_from_og(og) == 169
assert threads_from_og("1 234 подписчика • 1 ветка • био") == 1
assert threads_from_og("12K Followers • 85 threads • bio") == 85
assert threads_from_og("5 подписчиков • био про 3 ветки") is None  # число из био не берём
assert threads_from_og(None) is None

# 4. Локация из текста
title = "Сара |Риэлтор|Астана | Специалист (@nedvizhimost) • Threads — выразите свое мнение"
assert clean_title(title) == "Сара |Риэлтор|Астана | Специалист"
assert clean_description(og) == "Покупка | продажа"
assert text_location(title=title) == ("KZ Астана", "title")
assert text_location(bio="Ташкент", og_description=og) == ("UZ Ташкент", "bio")
# источники по порядку приоритета; страна без города уходит, если город той же страны назван
assert text_location(title=title, bio="Казахстан, Ташкент") == ("KZ Астана, UZ Ташкент", "title,bio")
assert text_location(title=title, sources=("bio",)) == (None, None)  # источник выключен
assert text_location(bio="барахолка") == (None, None)
# ветки: только по флагу, берутся самые частые места
places = [("UZ", "Ташкент", 9), ("KZ", "Алматы", 3)]
assert text_location(thread_places=places) == (None, None)
assert text_location(thread_places=places, sources=("threads",)) == (
    "UZ Ташкент, KZ Алматы",
    "threads",
)
assert text_location(bio="Астана", thread_places=places, sources=("bio", "threads"))[1] == (
    "bio,threads"
)
assert "threads" not in DEFAULT_TEXT_SOURCES
assert parse_text_sources(None) == DEFAULT_TEXT_SOURCES
assert parse_text_sources("threads, BIO") == ("bio", "threads")
try:
    parse_text_sources("bio,tittle")
except ValueError as e:
    assert "tittle" in str(e)
else:
    raise AssertionError("опечатка в источнике должна быть ошибкой")

# 5. Лист выгрузки
cols = [
    "id", "название", "ссылка", "заголовок", "подписчиков", "веток", "просмотров", "bio",
    "дата создания", "локация из био", "локация из текста", "темы", "топики",
]  # fmt: skip
sql = BASE_QUERIES["profiles"].sql
pos = [sql.index(f'AS "{c}"') if c != "id" else sql.index("p.id,") for c in cols]
assert pos == sorted(pos), "колонки листа идут не в заданном порядке"
assert "profiles-full" in BASE_QUERIES and "p.review_status" in BASE_QUERIES["profiles-full"].sql

print("textloc_check: OK")
