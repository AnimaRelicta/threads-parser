"""Фильтр «олх»: есть ли olx/олх в тексте поста или в его теме."""

import re

# Версия правил: пишется в posts.filter_version, чтобы после правки правил можно было
# пересчитать только посты, отфильтрованные по старой версии.
FILTER_VERSION = 1

# Приводим похожие символы смешанной раскладки к кириллице, чтобы
# «оЛХ», «oLx», «OLX», «оlx» распознавались одинаково.
# Цифру 1 не трогаем: вариант «ол1х» обрабатывается отдельным ключевым словом.
FILTER_TRANS = str.maketrans(
    {
        "o": "о",
        "O": "о",
        "l": "л",
        "L": "л",
        "x": "х",
        "X": "х",
    }
)

# Ищем именно целые слова (границы \b), чтобы «полха» и т.п. не считались совпадением.
FILTER_RE = re.compile(r"\b(?:олх|ол1х)\b")


def normalize_text(s: str) -> str:
    """Нижний регистр + унификация похожих символов латиницы/кириллицы."""
    return s.lower().translate(FILTER_TRANS)


def classify(text: str | None, topic: str | None) -> tuple[str, str | None, str | None]:
    """(filter_status, filter_matched, filter_matched_in).

    Сначала проверяется текст, потом тема. Запрос (query) намеренно не учитывается: иначе
    все посты по запросу «олх…» попадали бы в approved, даже если пост не про олх.
    filter_matched: что именно сработало, как написано в посте («olx», «олх»).
    """
    for where, value in (("text", text), ("topic", topic)):
        if not value:
            continue
        norm = normalize_text(value)
        m = FILTER_RE.search(norm)
        if m:
            raw = value[m.start() : m.end()] if len(norm) == len(value) else m.group(0)
            return "approved", raw.lower(), where
    return "rejected", None, None
