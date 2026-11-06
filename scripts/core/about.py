"""Разбор окна «Об этом профиле» (Threads: «···» → «Об этом профиле»).

Окно состоит из пар «заголовок → значение»: Имя/название, Дата регистрации,
Местоположение и т.д. (набор строк зависит от профиля и языка интерфейса).
Модуль чисто текстовый, без браузера: на вход пары [(заголовок, значение), ...].
"""

import re
from dataclasses import dataclass, field

# Заголовки строк (в нижнем регистре) по языкам интерфейса -> наше поле
_LABELS = {
    "name": ("имя/название", "имя", "name"),
    "joined": ("дата регистрации", "date joined", "joined"),
    "location": ("местоположение", "страна или регион", "based in", "location"),
}
# Все известные заголовки (нижний регистр): по ним окно читается независимо от вёрстки.
# Новый язык или строка в окне: допишите заголовок сюда (или он найдётся по жирному шрифту).
KNOWN_LABELS = tuple(v for variants in _LABELS.values() for v in variants) + (
    "учётная запись",
    "аккаунт",
    "account",
    "verified",
    "подтверждено",
    "бизнес-аккаунт",
    "ссылки в профиле",
)
_NAME_USER_RE = re.compile(r"^(?P<name>.*?)\s*\(@(?P<username>[A-Za-z0-9._]+)\)?\s*$")


@dataclass
class About:
    name: str | None = None  # отображаемое имя («Минская квартира»)
    username: str | None = None  # ник из скобок «(@by_kvartiraminsk)»
    joined: str | None = None  # «Октябрь 2026 г.» (часть до «·»)
    location: str | None = None  # «Беларусь»
    raw: dict[str, str] = field(default_factory=dict)  # все строки как есть: заголовок -> значение

    def __bool__(self) -> bool:
        return bool(self.raw)


def _field_of(label: str) -> str | None:
    low = label.strip().lower()
    for key, variants in _LABELS.items():
        if low in variants:
            return key
    return None


def parse_about(rows: list[tuple[str, str]] | list[list[str]]) -> About:
    """Пары (заголовок, значение) из окна -> About. Неизвестные строки остаются в raw."""
    about = About()
    for label, value in rows:
        label, value = (label or "").strip(), (value or "").strip()
        if not label or not value:
            continue
        about.raw[label] = value
        key = _field_of(label)
        if key == "name":
            m = _NAME_USER_RE.match(value)
            if m:
                about.name = m["name"] or None
                about.username = m["username"]
            else:
                about.name = value
        elif key == "joined":
            # «Октябрь 2026 г. · Более 100 млн»: после «·» идёт хвост Threads, дата — до него
            about.joined = value.split("·")[0].strip() or None
        elif key == "location":
            about.location = value
    return about
