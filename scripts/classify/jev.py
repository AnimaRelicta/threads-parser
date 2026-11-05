"""Jev: контекст профиля, вопросы и решение «класс или Прочее». Без БД и сети.

Jev (Choice) всегда возвращает класс с наибольшей вероятностью, даже когда ни один не подходит.
Поэтому после ответа работает защита: итог = `other` (Прочее), если
  1) лучший класс уже `other`;
  2) вероятность лучшего класса ниже JEV_MIN_PROB;
  3) confidence ответа ниже JEV_MIN_CONFIDENCE;
  4) вторая проверка (Noul «подходит ли хоть один класс 1-6») ниже JEV_NOUL_MIN.
Все вероятности сохраняются в profiles.category_raw, пороги можно пересмотреть без новых запросов.
При любой правке текста вопросов поднимайте PROMPT_VERSION (profiles.category_prompt_version).
"""

import json
from dataclasses import dataclass, field

from core.geo import clean_description

PROMPT_VERSION = 101  # 1xx = Jev; версии LLM-классификатора (prompts.py) идут с 1
FALLBACK = "other"
COUNTRY = "Казахстан"  # профили отфильтрованы по КЗ заранее: about_location всегда он
CHOICE_ID = "category"
NOUL_ID = "fits_specific"

INSTRUCTIONS = (
    "Определи категорию аккаунта (профиля) в Threads по его имени, описанию и темам. "
    "Выбери ровно один вариант. Если ни одна из категорий явно не подходит или данных слишком "
    'мало, выбери "other"; не подгоняй аккаунт под ближайшую категорию.'
)
NOUL_INSTRUCTIONS = (
    "Из имени, описания и тем аккаунта явно видно, что это магазин или бренд, барахолка, "
    "перекупщик, новостной канал, домовой чат или услуги. Ответ нет, если данных мало или "
    "аккаунт похож на личный блог, юмор, афишу, вакансии, крипто, бота или спам."
)


@dataclass
class Gate:
    """Пороги защиты от ложного класса."""

    min_prob: float = 0.6
    min_confidence: float = 0.5
    noul_gate: bool = True
    noul_min: float = 0.5


@dataclass
class Decision:
    category: str
    reason: str | None = None  # None = класс принят; иначе почему итог `other`
    top: str | None = None  # лучший класс по вероятности до защиты
    top_prob: float | None = None
    confidence: float | None = None
    noul: float | None = None
    probabilities: dict = field(default_factory=dict)


def _clean(v) -> str | None:
    s = " ".join(str(v).split()) if v else ""
    return s or None


def pick_bio(bio: str | None, og_description: str | None) -> tuple[str | None, str | None]:
    """(текст, источник): bio, а если пусто, то og_description без счётчиков подписчиков."""
    if b := _clean(bio):
        return b, "bio"
    if d := _clean(clean_description(og_description)):
        return d, "og_description"
    return None, None


def topics_list(topics) -> list[str]:
    """profiles.topics ([{text, query, tag_id}], JSONB) -> тексты тем."""
    if isinstance(topics, str):
        try:
            topics = json.loads(topics)
        except json.JSONDecodeError:
            return []
    out = []
    for t in topics or []:
        text = _clean(t.get("text") if isinstance(t, dict) else t)
        if text and text not in out:
            out.append(text)
    return out


def build_state(row) -> tuple[str | None, dict]:
    """Контекст для Jev и разбор по источникам; (None, ...) = текста нет, профиль не берём.

    Строки: имя (about_name), описание (bio или og_description), темы, страна. Страна не
    считается данными: профиль без имени, описания и тем классифицировать нечем.
    """
    name = _clean(row["about_name"])
    bio, bio_src = pick_bio(row["bio"], row["og_description"])
    topics = topics_list(row["topics"])
    info = {"name": bool(name), "bio_src": bio_src, "topics": len(topics)}
    if not (name or bio or topics):
        return None, info
    lines = []
    if name:
        lines.append(f"Имя: {name}")
    if bio:
        lines.append(f"Описание: {bio}")
    if topics:
        lines.append(f"Темы: {', '.join(topics)}")
    lines.append(f"Местоположение: {COUNTRY}")
    return "\n".join(lines), info


def build_questions(
    categories: list[tuple[str, str, str | None]],
    noul_gate: bool,
    instructions: str = INSTRUCTIONS,
    noul_instructions: str = NOUL_INSTRUCTIONS,
) -> dict:
    """Choice по всем активным категориям (имя варианта = код) и, по флагу, Noul-проверка."""
    criteria = {code: f"{name}. {desc or ''}".strip() for code, name, desc in categories}
    questions = {CHOICE_ID: {"type": "choice", "instructions": instructions, "criteria": criteria}}
    if noul_gate:
        questions[NOUL_ID] = {"type": "noul", "instructions": noul_instructions}
    return questions


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def decide(answers: dict, valid_codes: set[str], gate: Gate) -> Decision:
    """Ответ Jev -> итоговая категория с учётом порогов. Неразобранный ответ = `other`."""
    ans = answers.get(CHOICE_ID) or {}
    probs = {
        k: p
        for k, v in (ans.get("probabilities") or {}).items()
        if (p := _num(v)) is not None and k in valid_codes
    }
    conf = _num(ans.get("confidence"))
    noul = _num((answers.get(NOUL_ID) or {}).get("noul")) if gate.noul_gate else None
    if not probs:
        return Decision(FALLBACK, "no_answer", confidence=conf, noul=noul)
    top = max(probs, key=probs.__getitem__)  # класс с наибольшей вероятностью
    d = Decision(top, None, top, probs[top], conf, noul, probs)
    if top == FALLBACK:
        d.reason = "jev_other"
    elif probs[top] < gate.min_prob:
        d.reason = "low_prob"
    elif conf is not None and conf < gate.min_confidence:
        d.reason = "low_confidence"
    elif gate.noul_gate and (noul is None or noul < gate.noul_min):
        d.reason = "noul"
    if d.reason:
        d.category = FALLBACK
    return d
