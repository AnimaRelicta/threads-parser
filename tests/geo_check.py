"""Проверка геолокаций без сети и БД. Запуск: uv run python tests/geo_check.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.geo import (  # noqa: E402
    Signal,
    about_country,
    country_from_phone,
    find_locations,
    phone_prefix,
    post_signals,
    profile_signals,
    resolve,
)


def cities(text: str) -> set[str | None]:
    return {m.city for m in find_locations(text)}


# Склонения и написания
assert cities("квартира в Алматы") == {"Алматы"}
assert cities("приехал из Ташкента") == {"Ташкент"}
assert cities("живу в Астане, работаю в Шымкенте") == {"Астана", "Шымкент"}
assert cities("жительница Алма-Аты и Усть-Каменогорска") == {"Алматы", "Усть-Каменогорск"}
assert cities("moved to Tashkent, then Almaty") == {"Ташкент", "Алматы"}
assert cities("Nur-Sultan, Kazakhstan") == {"Астана", None}
assert cities("Нур-Султан и Астана") == {"Астана"}  # одно название = один город
m = find_locations("в Казахстане")
assert [(x.country, x.city) for x in m] == [("KZ", None)]
assert find_locations("из Узбекистана")[0].country == "UZ"

# Ложные срабатывания: обычные слова и фамилии
for bad in (
    "он орал на весь двор",
    "oral care",
    "много семей в доме",  # «семей» = мн. ч. «семья»
    "кокон бабочки",
    "рудный запах",
    "Андрей Шевченко",
    "гурьев пирог",
    "Нурсултан Иванов",
    "ташкентский плов",  # прилагательное, а не упоминание
    "картаган",
    "",
    None,
):
    assert find_locations(bad) == [], bad
assert cities("Живу в Семее") == {"Семей"}  # заглавная = город
assert cities("Семей — красивый город") == {"Семей"}

# Телефоны: КЗ 6xx/7xx, УЗ 998, РФ и неоднозначное не KZ
assert country_from_phone("77085826725") == "KZ"
assert country_from_phone("+7 (777) 123-45-67") == "KZ"
assert country_from_phone("76012345678") == "KZ"
assert country_from_phone("998901234567") == "UZ"
assert country_from_phone("79161234567") is None  # РФ
assert country_from_phone("78121234567") is None  # РФ, Санкт-Петербург
assert country_from_phone("89161234567") is None  # национальный формат: неоднозначно
assert country_from_phone("7085826725") is None  # без кода страны / не та длина
assert country_from_phone("99890123") is None  # слишком короткий
assert country_from_phone(None) is None
assert phone_prefix("77085826725") == "+7708" and phone_prefix("998901234567") == "+998"

# Сигналы профиля: about=Беларусь сохраняется как OTHER, а не «нет данных»
sig = profile_signals(
    about_location="Беларусь",
    links=[{"kind": "whatsapp", "handle": "77085826725"}, {"kind": "instagram", "handle": "x"}],
    bio=None,
    about_name="Квартира в Алматы",
    topics=[{"text": "Ташкент"}, {"text": "Недвижимость"}],
)
assert about_country(sig) == "OTHER"
assert {(s.source, s.country) for s in sig} == {
    ("about_location", "OTHER"),
    ("phone", "KZ"),
    ("about_name", "KZ"),
    ("topic", "UZ"),
}
assert all("708" not in s.matched_text.replace("+7708", "") for s in sig)  # номер не хранится
r = resolve(sig)
assert (r.country, r.source, r.conflict) == ("OTHER", "about_location", True)  # не молча
assert "phone=KZ" in "; ".join(f"{k}={v}" for k, v in r.by_source.items())

# Итог: самый надёжный источник, город из источника с той же страной
sig = profile_signals(
    about_location="Казахстан",
    links=[{"kind": "whatsapp", "handle": "77085826725"}],
    bio="Алматы",
    about_name=None,
    topics=None,
)
r = resolve(sig)
assert (r.country, r.city, r.source, r.conflict) == ("KZ", "Алматы", "about_location", False)
assert about_country(sig) == "KZ"
only_phone = profile_signals(
    about_location=None,
    links=[{"kind": "whatsapp", "handle": "998901234567"}],
    bio=None,
    about_name=None,
    topics=None,
)
assert resolve(only_phone).country == "UZ" and resolve(only_phone).source == "phone"

# Внутри одного источника разные страны: конфликт, итог из следующего однозначного
r = resolve(
    [
        Signal("bio", "KZ", "Алматы", "Алматы"),
        Signal("bio", "UZ", "Ташкент", "Ташкент"),
        Signal("topic", "UZ", None, "Узбекистан"),
    ]
)
assert (r.country, r.source, r.conflict) == ("UZ", "topic", True)

# Нет сигналов
empty = resolve(profile_signals(about_location=None, links=[], bio="", about_name=None, topics=[]))
assert empty.country is None and not empty.conflict

# Посты: слабый сигнал, в resolve не участвует
ps = post_signals("Сдам квартиру в Алматы")
assert [(s.source, s.city) for s in ps] == [("post_text", "Алматы")]
assert resolve(ps).country is None
print("OK")
