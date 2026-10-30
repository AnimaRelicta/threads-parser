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
from dataclasses import dataclass, field

DETECTOR_VERSION = 2

# город (канонiческое имя) -> (страна, [варианты написания, без учёта регистра])
CITIES: dict[str, tuple[str, list[str]]] = {
    # --- Казахстан: столица + крупнейшие/областные города ---
    "Астана": (
        "KZ",
        ["астана", "astana", "нур-султан", "nur-sultan", "нур султан", "nur sultan", "целиноград"],
    ),
    "Алматы": ("KZ", ["алматы", "almaty", "алма-ата", "алма ата"]),
    "Шымкент": ("KZ", ["шымкент", "shymkent", "чимкент"]),
    "Караганда": ("KZ", ["караганда", "karaganda", "караганды", "qaraghandy"]),
    "Актобе": ("KZ", ["актобе", "aktobe", "актюбинск"]),
    "Тараз": ("KZ", ["тараз", "taraz"]),
    "Павлодар": ("KZ", ["павлодар", "pavlodar"]),
    "Усть-Каменогорск": ("KZ", ["усть-каменогорск", "ust-kamenogorsk", "оскемен", "өскемен"]),
    "Семей": ("KZ", ["семей", "семее", "семея", "семею", "semey", "семипалатинск"]),
    "Атырау": ("KZ", ["атырау", "atyrau"]),
    "Костанай": ("KZ", ["костанай", "kostanay", "кустанай"]),
    "Кызылорда": ("KZ", ["кызылорда", "kyzylorda", "кзыл-орда"]),
    "Уральск": ("KZ", ["уральск", "uralsk"]),
    "Петропавловск": ("KZ", ["петропавловск", "petropavlovsk", "петропавл"]),
    "Актау": ("KZ", ["актау", "aktau"]),
    "Темиртау": ("KZ", ["темиртау", "temirtau"]),
    "Туркестан": ("KZ", ["туркестан", "turkestan", "түркістан"]),
    "Кокшетау": ("KZ", ["кокшетау", "kokshetau", "кокчетав"]),
    "Талдыкорган": ("KZ", ["талдыкорган", "taldykorgan", "талдықорған"]),
    "Экибастуз": ("KZ", ["экибастуз", "ekibastuz"]),
    "Жезказган": ("KZ", ["жезказган", "zhezkazgan", "жезқазған"]),
    "Рудный": ("KZ", ["рудный", "рудном", "rudny"]),
    # --- Узбекистан: столица + крупнейшие/областные города ---
    "Ташкент": ("UZ", ["ташкент", "tashkent", "toshkent"]),
    "Самарканд": ("UZ", ["самарканд", "samarkand", "samarqand"]),
    "Наманган": ("UZ", ["наманган", "namangan"]),
    "Андижан": ("UZ", ["андижан", "andijan", "andijon"]),
    "Нукус": ("UZ", ["нукус", "nukus", "nókis"]),
    "Фергана": ("UZ", ["фергана", "fergana", "farg'ona", "фаргона"]),
    "Бухара": ("UZ", ["бухара", "bukhara", "buxoro"]),
    "Карши": ("UZ", ["карши", "karshi", "qarshi"]),
    "Коканд": ("UZ", ["коканд", "kokand", "qoʻqon"]),
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


# Слова, которые совпадают с обычными: ищем только с заглавной буквы («семей» — мн. ч. от «семья»)
CAPITAL_ONLY = {"семей", "семее", "семея", "семею", "рудный", "рудном"}

_CYR = re.compile(r"[а-яёәіңғүұқөһ]")
_END_A = "а|ы|е|у|ой|ою"  # Астана -> в Астане, из Астаны
_END_CONS = "а|у|е|ом|ем"  # Ташкент -> из Ташкента, в Ташкенте


def _alias_pattern(alias: str) -> re.Pattern:
    """Паттерн варианта написания: границы слова, русские падежные окончания, регистр."""
    body = re.escape(alias)
    if _CYR.search(alias):
        if alias.endswith("а"):
            body = re.escape(alias[:-1]) + f"(?:{_END_A})"
        elif alias[-1] not in "ыиуоеюяйь":
            body += f"(?:{_END_CONS})?"
    if alias in CAPITAL_ONLY:
        body = f"(?-i:{body[0].upper()}){body[1:]}"
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _compile() -> list[tuple[re.Pattern, str, str | None]]:
    """Один общий список (паттерн, country, city) — города проверяются раньше стран,
    т.к. упоминание города уже достаточно конкретно само по себе."""
    rules: list[tuple[re.Pattern, str, str | None]] = []
    for city, (country, aliases) in CITIES.items():
        for alias in aliases:
            rules.append((_alias_pattern(alias), country, city))
    for _name, (country, aliases) in COUNTRIES.items():
        for alias in aliases:
            rules.append((_alias_pattern(alias), country, None))
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


# --- Сигналы профиля -----------------------------------------------------------------

OTHER = "OTHER"  # страна указана, но это не КЗ/УЗ (about_location = «Беларусь»)
# Источники по убыванию надёжности; post_text слабый и в итог не входит
RELIABLE = ("about_location", "phone", "bio", "about_name", "topic")
POST_TEXT = "post_text"


@dataclass(frozen=True)
class Signal:
    source: str  # about_location | phone | bio | about_name | topic | post_text
    country: str  # KZ | UZ | OTHER
    city: str | None
    matched_text: str


def country_from_phone(phone: str | None) -> str | None:
    """Страна по номеру цифрами (WhatsApp): 998 -> UZ; 7 + 6xx/7xx -> KZ.

    Россия тоже начинается с 7 (9xx, 8xx, 4xx, 3xx), поэтому при любой неоднозначности
    (другая длина, 8 вместо 7, номер без кода страны) возвращаем None, а не KZ.
    """
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("998") and len(d) == 12:
        return "UZ"
    if d.startswith("7") and len(d) == 11 and d[1] in "67":
        return "KZ"
    return None


def phone_prefix(phone: str) -> str:
    """Начало номера для matched_text: сам номер в БД не кладём («+7708», «+998»)."""
    d = re.sub(r"\D", "", phone)
    return "+998" if d.startswith("998") else f"+{d[:4]}"


def _from_text(source: str, text: str | None) -> list[Signal]:
    return [Signal(source, m.country, m.city, m.matched_text) for m in find_locations(text)]


def profile_signals(
    *,
    about_location: str | None,
    links: list[dict] | None,
    bio: str | None,
    about_name: str | None,
    topics: list[dict] | None,
) -> list[Signal]:
    """Все геосигналы профиля из уже собранных полей (без сети и БД)."""
    out: list[Signal] = []
    place = (about_location or "").strip()
    if place:
        found = _from_text("about_location", place)
        out += found or [Signal("about_location", OTHER, None, place)]
    for link in links or []:
        if link.get("kind") != "whatsapp" or not link.get("handle"):
            continue
        country = country_from_phone(link["handle"])
        if country:
            out.append(Signal("phone", country, None, phone_prefix(link["handle"])))
    out += _from_text("bio", bio)
    out += _from_text("about_name", about_name)
    for t in topics or []:
        out += _from_text("topic", t.get("text"))
    return out


def post_signals(text: str | None) -> list[Signal]:
    return _from_text(POST_TEXT, text)


def about_country(signals: list[Signal]) -> str | None:
    """Страна из about_location (KZ|UZ|OTHER) для profiles.about_country."""
    return next((s.country for s in signals if s.source == "about_location"), None)


@dataclass
class Resolved:
    country: str | None = None  # KZ | UZ | OTHER | None (нет данных)
    city: str | None = None
    source: str | None = None  # откуда взят итог
    conflict: bool = False  # надёжные источники называют разные страны
    by_source: dict[str, str] = field(default_factory=dict)  # источник -> «KZ Алматы»


def resolve(signals: list[Signal]) -> Resolved:
    """Итог профиля: самый надёжный источник, конфликты не скрываются.

    Источник, внутри которого названы разные страны, считается неоднозначным: это конфликт,
    итог берётся из следующего однозначного источника. post_text сюда не входит.
    """
    res = Resolved()
    picked: list[tuple[str, str]] = []  # (источник, страна) однозначных источников
    for source in RELIABLE:
        sigs = [s for s in signals if s.source == source]
        if not sigs:
            continue
        countries = list(dict.fromkeys(s.country for s in sigs))
        res.by_source[source] = ", ".join(
            dict.fromkeys(f"{s.country} {s.city}" if s.city else s.country for s in sigs)
        )
        if len(countries) > 1:
            res.conflict = True
        else:
            picked.append((source, countries[0]))
    if len({c for _, c in picked}) > 1:
        res.conflict = True
    if picked:
        res.source, res.country = picked[0]
        for source, country in picked:
            city = next(
                (s.city for s in signals if s.source == source and s.country == country and s.city),
                None,
            )
            if country == res.country and city:
                res.city = city
                break
    return res
