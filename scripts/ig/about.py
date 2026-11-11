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


def is_about_text(text: str | None) -> bool:
    """Похоже ли это на окно «Об аккаунте»: есть хотя бы один известный заголовок."""
    lines = {norm(ln) for ln in (text or "").splitlines()}
    return any(lab in lines for labs in ABOUT_LABELS.values() for lab in labs)


def parse_about_text(text: str | None) -> dict:
    """Поля окна: joined_*, account_location, verified_*, is_verified и rows (как в окне).

    Значение — следующая непустая строка после заголовка; поля без заголовка не заполняются."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    all_labels = {lab for labs in ABOUT_LABELS.values() for lab in labs}
    found: dict[str, tuple[str, str | None]] = {}
    for i, ln in enumerate(lines):
        for key, labs in ABOUT_LABELS.items():
            if norm(ln) in labs and key not in found:
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                found[key] = (ln, None if nxt is None or norm(nxt) in all_labels else nxt)
    out: dict = {"rows": [[label, value] for label, value in found.values()]}
    if "joined" in found:
        out["joined_text"] = found["joined"][1]
        out["joined_date"] = parse_month_year(found["joined"][1])
    if "location" in found:
        out["account_location"] = found["location"][1]
    if "verified" in found:
        out["is_verified"] = True
        out["verified_text"] = found["verified"][1]
        out["verified_date"] = parse_month_year(found["verified"][1])
    return out
