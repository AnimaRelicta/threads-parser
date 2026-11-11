"""Окно «Об аккаунте» без браузера: строки «заголовок → значение» из текста окна."""

import re
from datetime import date

# Заголовки (нижний регистр, «ё» → «е»). Окно локализовано под язык аккаунта.
ABOUT_LABELS = {
    "joined": ("дата присоединения", "date joined"),
    "location": ("местоположение аккаунта", "account based in"),
    "verified": ("аккаунт подтвержден", "verified"),
}
MONTHS = {
    **{m: i for i, m in enumerate(
        "январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split(),
        1,
    )},
    **{m: i for i, m in enumerate(
        "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
        1,
    )},
    **{m: i for i, m in enumerate(
        "january february march april may june july august september october november december"
        .split(),
        1,
    )},
}
_MONTH_YEAR = re.compile(r"([^\W\d_]+)\.?\s+(\d{4})")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip().lower().replace("ё", "е")


def parse_month_year(s: str | None) -> date | None:
    """'Октябрь 2013 г.' / 'October 2013' -> 2013-10-01 (в окне нет дня)."""
    m = _MONTH_YEAR.search(s or "")
    month = MONTHS.get(norm(m.group(1))) if m else None
    return date(int(m.group(2)), month, 1) if m and month else None


_STOP = ("значок подтверждения", "the verified badge", "подробнее", "learn more", "закрыть", "close")
# Заголовки, которые безопасно искать в сплошном тексте (без слова «verified» из пояснений)
_FLAT_LABELS = {
    "joined": ABOUT_LABELS["joined"],
    "location": ABOUT_LABELS["location"],
    "verified": ("аккаунт подтвержден", "account verified"),
}
LOCATION_MAX = 80


def flat(text: str | None) -> str:
    """Текст окна в одну строку: окно Instagram (Bloks) отдаёт заголовок и значение без
    разрывов строк, а иногда и в одном блоке."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _label_spans(text: str) -> dict[str, tuple[int, int]]:
    """key -> (начало, конец) первого вхождения заголовка в сплошном тексте (без «ё» и регистра)."""
    low = text.lower().replace("ё", "е")  # длина строки не меняется
    spans: dict[str, tuple[int, int]] = {}
    for key, labs in _FLAT_LABELS.items():
        hits = [(low.find(lab), lab) for lab in labs if lab in low]
        if hits:
            pos, lab = min(hits)
            spans[key] = (pos, pos + len(lab))
    return spans


def is_about_text(text: str | None) -> bool:
    """Похоже ли это на окно «Об аккаунте»: есть хотя бы один известный заголовок
    (отдельной строкой или внутри сплошного текста)."""
    lines = {norm(ln) for ln in (text or "").splitlines()}
    if any(lab in lines for labs in ABOUT_LABELS.values() for lab in labs):
        return True
    spans = _label_spans(flat(text))
    return "joined" in spans or "location" in spans or "verified" in spans and bool(
        parse_month_year(flat(text)[spans["verified"][1]:][:40])
    )


def _flat_fields(text: str) -> dict[str, str | None]:
    """Значения заголовков из сплошного текста: от конца заголовка до начала следующего."""
    spans = _label_spans(text)
    starts = sorted(a for a, _ in spans.values())
    low = text.lower().replace("ё", "е")
    out: dict[str, str | None] = {}
    for key, (_, end) in spans.items():
        nxt = [a for a in starts if a >= end]
        seg = text[end : min(nxt) if nxt else len(text)]
        cut = min((i for st in _STOP if (i := low.find(st, end, end + len(seg))) >= 0), default=None)
        if cut is not None:
            seg = text[end:cut]
        seg = seg.strip(" :·-")
        if key in ("joined", "verified"):
            head = seg[:40]
            m = _MONTH_YEAR.search(head)
            seg = ""
            if m:
                seg = m.group(0)
                if re.match(r"\s*г\.?", head[m.end():]):
                    seg += " г."
        else:
            seg = seg[:LOCATION_MAX].strip()
        out[key] = seg or None
    return out


def parse_about_text(text: str | None) -> dict:
    """Поля окна: joined_*, account_location, verified_*, is_verified и rows (как в окне).

    Сначала сплошной текст (заголовки и значения без разрывов строк), так работает окно
    Instagram. Значение — текст до следующего заголовка; для дат берётся «Месяц ГГГГ [г.]»."""
    found: dict[str, tuple[str, str | None]] = {}
    flat_text = flat(text)
    spans = _label_spans(flat_text)
    values = _flat_fields(flat_text)
    for key in sorted(spans, key=lambda k: spans[k][0]):
        a, b = spans[key]
        found[key] = (flat_text[a:b], values.get(key))
    out: dict = {"rows": [[label, value] for label, value in found.values()]}
    if "joined" in found:
        out["joined_text"] = found["joined"][1]
        out["joined_date"] = parse_month_year(found["joined"][1])
    if "location" in found:
        out["account_location"] = found["location"][1]
    if "verified" in found and parse_month_year(found["verified"][1]):
        out["is_verified"] = True
        out["verified_text"] = found["verified"][1]
        out["verified_date"] = parse_month_year(found["verified"][1])
    return out
