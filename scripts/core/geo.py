"""Справочник городов/стран Казахстана и Узбекистана и поиск их упоминаний в тексте.

Таргет — два рынка: Казахстан (KZ) и Узбекистан (UZ). Справочник собран вручную:
столицы + крупнейшие/областные города, самые ходовые варианты написания (кириллица,
латиница, альтернативные транслитерации). Это не исчерпывающий геосправочник, а
рабочий список под задачу поиска «где находится автор».

DETECTOR_VERSION поднимается при любой правке CITIES/COUNTRIES — так же, как
FILTER_VERSION в core/textfilter.py, чтобы `geo.py rescan --force` знал, что
пересчитывать.
"""

import re
from dataclasses import dataclass

DETECTOR_VERSION = 1

# город (канонiческое имя) -> (страна, [варианты написания, без учёта регистра])
CITIES: dict[str, tuple[str, list[str]]] = {
    # --- Казахстан: столица + крупнейшие/областные города ---
    "Астана": ("KZ", ["астана", "astana", "нур-султан", "nur-sultan", "нурсултан", "целиноград"]),
    "Алматы": ("KZ", ["алматы", "almaty", "алма-ата", "алма ата"]),
    "Шымкент": ("KZ", ["шымкент", "shymkent", "чимкент"]),
    "Караганда": ("KZ", ["караганда", "karaganda", "караганды", "qaraghandy"]),
    "Актобе": ("KZ", ["актобе", "aktobe", "актюбинск"]),
    "Тараз": ("KZ", ["тараз", "taraz", "джамбул"]),
    "Павлодар": ("KZ", ["павлодар", "pavlodar"]),
    "Усть-Каменогорск": ("KZ", ["усть-каменогорск", "ust-kamenogorsk", "оскемен", "өскемен"]),
    "Семей": ("KZ", ["семей", "semey", "семипалатинск"]),
    "Атырау": ("KZ", ["атырау", "atyrau", "гурьев"]),
    "Костанай": ("KZ", ["костанай", "kostanay", "кустанай"]),
    "Кызылорда": ("KZ", ["кызылорда", "kyzylorda", "кзыл-орда"]),
    "Уральск": ("KZ", ["уральск", "uralsk", "орал", "oral"]),
    "Петропавловск": ("KZ", ["петропавловск", "petropavlovsk", "петропавл"]),
    "Актау": ("KZ", ["актау", "aktau", "шевченко"]),
    "Темиртау": ("KZ", ["темиртау", "temirtau"]),
    "Туркестан": ("KZ", ["туркестан", "turkestan", "түркістан"]),
    "Кокшетау": ("KZ", ["кокшетау", "kokshetau", "кокчетав"]),
    "Талдыкорган": ("KZ", ["талдыкорган", "taldykorgan", "талдықорған"]),
    "Экибастуз": ("KZ", ["экибастуз", "ekibastuz"]),
    "Жезказган": ("KZ", ["жезказган", "zhezkazgan", "жезқазған"]),
    "Рудный": ("KZ", ["рудный", "rudny"]),
    # --- Узбекистан: столица + крупнейшие/областные города ---
    "Ташкент": ("UZ", ["ташкент", "tashkent", "toshkent"]),
    "Самарканд": ("UZ", ["самарканд", "samarkand", "samarqand"]),
    "Наманган": ("UZ", ["наманган", "namangan"]),
    "Андижан": ("UZ", ["андижан", "andijan", "andijon"]),
    "Нукус": ("UZ", ["нукус", "nukus", "nókis"]),
    "Фергана": ("UZ", ["фергана", "fergana", "farg'ona", "фаргона"]),
    "Бухара": ("UZ", ["бухара", "bukhara", "buxoro"]),
    "Карши": ("UZ", ["карши", "karshi", "qarshi"]),
    "Коканд": ("UZ", ["коканд", "kokand", "qoʻqon", "кокон"]),
    "Маргилан": ("UZ", ["маргилан", "margilan", "margʻilon"]),
    "Ургенч": ("UZ", ["ургенч", "urgench", "urganch"]),
    "Термез": ("UZ", ["термез", "termez", "termiz"]),
    "Джизак": ("UZ", ["джизак", "jizzakh", "jizzax"]),
    "Навои": ("UZ", ["навои", "navoi", "navoiy"]),
    "Гулистан": ("UZ", ["гулистан", "guliston", "gulistan"]),
    "Чирчик": ("UZ", ["чирчик", "chirchik", "chirchiq"]),
}

# страна -> варианты написания (кроме случаев, когда совпал город — тогда страна известна сама собой)
COUNTRIES: dict[str, tuple[str, list[str]]] = {
    "Казахстан": ("KZ", ["казахстан", "қазақстан", "kazakhstan", "qazaqstan"]),
    "Узбекистан": ("UZ", ["узбекистан", "oʻzbekiston", "o'zbekiston", "uzbekiston", "uzbekistan"]),
}


@dataclass(frozen=True)
class Match:
    country: str  # 'KZ' | 'UZ'
    city: str | None  # канонiческое имя города либо None, если совпала только страна
    matched_text: str  # фрагмент исходного текста (как он был написан)


def _compile() -> list[tuple[re.Pattern, str, str | None]]:
    """Один общий список (паттерн, country, city) — города проверяются раньше стран,
    т.к. упоминание города уже достаточно конкретно само по себе."""
    rules: list[tuple[re.Pattern, str, str | None]] = []
    for city, (country, aliases) in CITIES.items():
        for alias in aliases:
            rules.append((re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), country, city))
    for _name, (country, aliases) in COUNTRIES.items():
        for alias in aliases:
            rules.append((re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE), country, None))
    return rules


_RULES = _compile()


def find_locations(text: str | None) -> list[Match]:
    """Все упоминания городов/стран КЗ и УЗ в тексте. Пустой список, если текста нет
    или ничего не нашлось. Дедуплицирует по (country, city) — для текста важно, что
    город упомянут, а не сколько раз."""
    if not text:
        return []
    seen: set[tuple[str, str | None]] = set()
    out: list[Match] = []
    for pattern, country, city in _RULES:
        m = pattern.search(text)
        if m is None:
            continue
        key = (country, city)
        if key in seen:
            continue
        seen.add(key)
        out.append(Match(country=country, city=city, matched_text=m.group(0)))
    return out
