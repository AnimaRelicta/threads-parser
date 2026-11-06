#!/usr/bin/env python3
"""Смоук-тест моделей OpenRouter на ваших задачах: извлечение объявления и классификация канала.

Что проверяет для каждой модели:
  1. запускается ли вообще (ответ 200, structured output распарсился в вашу Pydantic-схему);
  2. даёт ли правильные ответы на небольшом наборе примеров (is_ad, цена, валюта, контакт,
     тип канала, язык);
  3. отключилось ли «мышление» (счётчик reasoning_tokens должен быть 0);
  4. сколько стоит прогон (usage.cost от OpenRouter) и сколько занимает по времени.

Системные промпты берутся из ваших файлов analyze.py и channel_classify.py (читаются через ast,
проект src.* для запуска не нужен). Вызов такой же, как в проде: chat.completions.parse +
response_format=<Pydantic> + плагин response-healing + usage.include.

Запуск:
    export OPENROUTER_API_KEY=sk-or-... 
    python smoke_models.py --llm-dir ./llm                # все модели по умолчанию
    python smoke_models.py --llm-dir ./llm --proxy http://host:port
    python smoke_models.py --llm-dir ./llm --models anthropic/claude-haiku-5.5 --verbose

Зависимости: openai>=1.x (v2 тоже подходит), pydantic, httpx (ставятся вместе с openai).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from openai import APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient, LengthFinishReasonError
from pydantic import BaseModel, Field, ValidationError

# ──────────────────────────────────────────────────────────────────────────────
# Модели и способ отключения мышления
# ──────────────────────────────────────────────────────────────────────────────
# Для каждой модели — лесенка вариантов параметра `reasoning` (OpenRouter). Берётся первый,
# который API не отклонил (HTTP 400/422); дальше он «закрепляется» за моделью.
#   {"enabled": False}      — полностью выключить мышление;
#   {"effort": "minimal"}   — минимальное усилие (если выключить нельзя);
#   None                    — параметр не отправлять (модель не поддерживает reasoning).
OFF = {"enabled": False}
MIN = {"effort": "minimal"}
LOW = {"effort": "low"}

DEFAULT_LADDER = [OFF, MIN, LOW, None]

MODELS: dict[str, list[dict | None]] = {
    "google/gemini-2.5-flash-lite": DEFAULT_LADDER,
    "anthropic/claude-haiku-5.5": DEFAULT_LADDER,
    "deepseek/deepseek-v4.1-flash-1017": DEFAULT_LADDER,
    "minimax/minimax-m3.1-flash": DEFAULT_LADDER,
    "z-ai/glm-5.3-flash": DEFAULT_LADDER,
    # Бонус: прямой преемник вашей модели у Google (у Gemini 3.x уровни: minimal/low/medium/high).
    "google/gemini-3.1-flash-lite": [MIN, OFF, LOW, None],
}

# ──────────────────────────────────────────────────────────────────────────────
# Схемы (повторяют ваши analyze.py / channel_classify.py)
# ──────────────────────────────────────────────────────────────────────────────


class ProductCard(BaseModel):
    title: str = Field(description="Краткое название товара/услуги на русском")
    category: str | None = Field(default=None, description="Тематическая категория")
    price: float | None = Field(default=None, description="Числовая цена без валюты; null если договорная/не указана")
    currency: str | None = Field(default=None, description="Валюта (UZS/USD/RUB); null если price=null")
    location: str | None = Field(default=None, description="Локация (город/район), если указана")
    condition: str | None = Field(default=None, description='"new" / "used" / null')
    contact: str | None = Field(default=None, description="Телефон или @username продавца из текста")


class AdAnalysis(BaseModel):
    is_ad: bool = Field(description="True — объявление о продаже товара/услуги; False — прочее")
    reason: str = Field(description="Краткое обоснование на русском")
    card: ProductCard | None = Field(default=None, description="Карточка товара, если is_ad=true; иначе null")


# Значения enum'ов — из системного промпта classify (в проекте это src.db.enums).
class ChannelType(str, Enum):
    BARAHOLKA = "Барахолка"
    DOMOVOY = "Домовой"
    PEREKUP = "Перекупщик"
    VITRINA = "Витрина"
    USLUGI = "Услуги"
    REPOSTY = "Репосты"
    NOVOSTI = "Новости"
    UNKNOWN = "Неизвестно"


class ChannelLanguage(str, Enum):
    UZ = "Узбекский"
    RU = "Русский"
    EN = "Английский"
    TG = "Таджикский"
    KZ = "Казахский"
    KK = "Каракалпакский"
    UNKNOWN = "Неизвестно"


class ChannelClassification(BaseModel):
    channel_type: ChannelType = Field(description="Тип/роль канала")
    languages: list[ChannelLanguage] = Field(
        description="Языки канала по убыванию доминирования; один — если моноязычный; [Неизвестно] — если мало текста"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность 0..1")
    reason: str = Field(description="Краткое обоснование на русском")


def load_system_prompt(path: Path) -> str:
    """Достаёт константу _SYSTEM_PROMPT из .py без импорта модуля."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SYSTEM_PROMPT" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"_SYSTEM_PROMPT не найден в {path}")


def build_channel_user_message(title: str | None, about: str | None, message_texts: list[str], max_chars: int = 800) -> str:
    """Копия build_channel_classify_user_message из вашего кода."""
    title_s = (title or "").strip() or "(не указано)"
    about_s = (about or "").strip() or "(не указано)"
    lines = [
        f"Название канала: {title_s}",
        f"Описание канала: {about_s}",
        f"Последние сообщения ({len(message_texts)} шт.):",
    ]
    for i, raw in enumerate(message_texts, start=1):
        text = " ".join(raw.split())
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        lines.append(f"{i}. {text or '(пусто)'}")
    if not message_texts:
        lines.append("(сообщений с текстом в выборке нет)")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Тестовые примеры (написаны с нуля под правила вашего промпта)
# ──────────────────────────────────────────────────────────────────────────────
# price: _UNSET — цену не проверяем; None — ожидаем null; число — ожидаем это значение.
_UNSET = object()

AD_CASES: list[dict] = [
    {
        "name": "uz-latin: продажа iPhone, цена в $",
        "text": "Sotiladi iPhone 13 128GB, yangi holatda. Narxi 450$. Toshkent, Chilonzor. Tel: +998 90 123 45 67",
        "is_ad": True, "price": 450, "currency": "USD", "contact_digits": "998901234567",
    },
    {
        "name": "ru: продам диван б/у, разделители тысяч",
        "text": "Продам диван б/у, хорошее состояние. Цена 1 200 000 сум. Самарканд. Писать @seller_sam",
        "is_ad": True, "price": 1200000, "currency": "UZS", "contact_text": "seller_sam",
    },
    {
        "name": "uz-кириллица: множитель «минг»",
        "text": "Сотилади Samsung музлатгич, ишлатилган. Нархи 900 минг сўм. Андижон. 91 555 12 34",
        "is_ad": True, "price": 900000, "currency": "UZS",
    },
    {
        "name": "ru: множитель «млн»",
        "text": "Срочно продаю ноутбук Lenovo, цена 6 млн сум, торг. Ташкент, Юнусабад. +998 93 111 22 33",
        "is_ad": True, "price": 6000000, "currency": "UZS",
    },
    {
        "name": "ru: цена договорная → price=null",
        "text": "Продаётся 3-комнатная квартира, Юнусабад, 5 этаж из 9, ремонт. Цена договорная. 90 111 22 33",
        "is_ad": True, "price": None, "currency": None,
    },
    {
        "name": "ru: цена только за единицу (сотку) → price=null",
        "text": "Продаю участок 6 соток в Чирчике, 15 000 $ за сотку. Звоните 90 777 88 99",
        "is_ad": True, "price": None, "currency": None,
    },
    {
        "name": "ru: запрос покупки (спрос) → не объявление",
        "text": "Куплю б/у ноутбук до 5 млн сум, Ташкент. Пишите в личку",
        "is_ad": False,
    },
    {
        "name": "uz: сделка завершена («Sotildi») → не объявление",
        "text": "iPhone 12, 64GB, narxi 400$, tel +998 90 000 00 00. Sotildi! Rahmat hammaga, e'lonni o'chirib tashlang",
        "is_ad": False,
    },
    {
        "name": "uz: болтовня → не объявление",
        "text": "Assalomu alaykum, bugun ob-havo qanday bo'ldi? Yomg'ir yog'adimi?",
        "is_ad": False,
    },
    {
        "name": "ru: аренда сдана → не объявление",
        "text": "Сдаётся 2-комн. квартира, 3 млн в месяц, Чиланзар. СДАНО, спасибо, объявление можно удалить",
        "is_ad": False,
    },
]

CHANNEL_CASES: list[dict] = [
    {
        "name": "барахолка (много разных продавцов, uz+ru)",
        "title": "Toshkent bozor | Барахолка",
        "about": "Hamma narsa sotiladi va olinadi. Реклама только платно.",
        "messages": [
            "Sotiladi velosiped, narxi 800 ming so'm. Tel 90 123 45 67",
            "Продам детскую коляску, 1 200 000 сум. Ташкент",
            "Куплю холодильник б/у недорого",
            "Sotaman iPhone 11, 350$, Yunusobod",
            "Продаётся стиральная машина LG, цена 2 500 000",
            "Ishlatilgan divan sotiladi, 1 mln so'm",
        ],
        "type": ChannelType.BARAHOLKA, "lang": ChannelLanguage.UZ,
    },
    {
        "name": "домовой чат ЖК (быт, соседи, русский)",
        "title": "ЖК Мирабад Авеню — соседи",
        "about": "Чат жильцов 3-й очереди",
        "messages": [
            "Подскажите, кто-нибудь знает, когда починят лифт во втором подъезде?",
            "У нас сегодня опять отключили воду, у вас так же?",
            "Соседи сверху шумят после 23:00, что делать?",
            "Кто может посоветовать хорошего сантехника в наш дом?",
            "Напоминаю, собрание жильцов завтра в 19:00 во дворе",
        ],
        "type": ChannelType.DOMOVOY, "lang": ChannelLanguage.RU,
    },
    {
        "name": "витрина магазина (один продавец, каталог)",
        "title": "TechPlaza.uz — магазин электроники",
        "about": "Официальный канал магазина. Доставка по Ташкенту. Гарантия 12 месяцев.",
        "messages": [
            "Новое поступление! Xiaomi 14T 256GB — 4 990 000 сум. В наличии, гарантия.",
            "Наушники Sony WH-1000XM5 — 3 200 000 сум. Заказ по телефону +998 71 200 00 00",
            "Акция недели: роутеры TP-Link со скидкой 15%. Адрес: ТЦ «Алмазар», 2 этаж",
            "Ноутбуки ASUS в рассрочку 0%, подробности в личных сообщениях",
        ],
        "type": ChannelType.VITRINA, "lang": ChannelLanguage.RU,
    },
    {
        "name": "новости/мемы без торговли (русский)",
        "title": "Ташкент Daily",
        "about": "Новости города, мемы, интересное",
        "messages": [
            "Сегодня в Ташкенте открыли новую станцию метро на Юнусабадской линии",
            "Мем дня: когда понял, что завтра понедельник 😂",
            "Погода на выходные: до +18, без осадков",
            "В центре города пройдёт фестиваль уличной еды",
        ],
        "type": ChannelType.NOVOSTI, "lang": ChannelLanguage.RU,
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Вызов модели
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class CallResult:
    model: str
    task: str  # "extract" | "classify"
    case: str
    started: bool = False       # API ответил 200 (модель запустилась)
    schema_ok: bool = False     # ответ распарсился в Pydantic-схему
    correct: bool | None = None  # совпал с ожиданием (None — не проверялось)
    notes: str = ""
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    reasoning_variant: str = ""
    error: str = ""
    parsed: dict | None = field(default=None, repr=False)


class ModelState:
    """Состояние на модель: какой вариант reasoning уже сработал, нужно ли убрать temperature."""

    def __init__(self, ladder: list[dict | None]):
        self.ladder = ladder
        self.idx = 0
        self.send_temperature = True
        self.lock = asyncio.Lock()

    @property
    def variant(self) -> dict | None:
        return self.ladder[min(self.idx, len(self.ladder) - 1)]


def variant_label(v: dict | None) -> str:
    return "без параметра reasoning" if v is None else json.dumps(v, ensure_ascii=False)


def _usage_stats(completion) -> tuple[int, int, int, float | None]:
    u = getattr(completion, "usage", None)
    if u is None:
        return 0, 0, 0, None
    details = getattr(u, "completion_tokens_details", None)
    reasoning = int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0
    extra = getattr(u, "model_extra", None) or {}
    cost = extra.get("cost")
    return int(u.prompt_tokens or 0), int(u.completion_tokens or 0), reasoning, (float(cost) if cost is not None else None)


async def call_model(
    client: AsyncOpenAI,
    state: ModelState,
    model: str,
    *,
    messages: list[dict],
    schema: type[BaseModel],
    temperature: float | None,
    max_tokens: int,
    require_parameters: bool,
):
    """Один structured-вызов. При HTTP 400/422 переходит по лесенке reasoning / убирает temperature."""
    last_exc: Exception | None = None
    for _ in range(len(state.ladder) + 2):
        extra_body: dict = {"plugins": [{"id": "response-healing"}], "usage": {"include": True}}
        variant = state.variant
        if variant is not None:
            extra_body["reasoning"] = variant
        if require_parameters:
            extra_body["provider"] = {"require_parameters": True}
        kwargs: dict = dict(
            model=model, messages=messages, response_format=schema, extra_body=extra_body, max_tokens=max_tokens
        )
        if temperature is not None and state.send_temperature:
            kwargs["temperature"] = temperature
        try:
            completion = await client.chat.completions.parse(**kwargs)
            return completion, variant
        except APIStatusError as e:
            last_exc = e
            if e.status_code not in (400, 422):
                raise
            body = str(getattr(e, "body", "") or e).lower()
            async with state.lock:
                if "temperature" in body and state.send_temperature and temperature is not None:
                    state.send_temperature = False
                elif state.idx < len(state.ladder) - 1 and variant is state.variant:
                    state.idx += 1  # параметр reasoning отклонён → следующий вариант
                else:
                    raise
    assert last_exc is not None
    raise last_exc


def check_extract(case: dict, parsed: AdAnalysis) -> tuple[bool, str]:
    problems: list[str] = []
    if parsed.is_ad != case["is_ad"]:
        problems.append(f"is_ad={parsed.is_ad}, ждали {case['is_ad']}")
    elif case["is_ad"]:
        card = parsed.card
        if card is None:
            problems.append("card=null при is_ad=true")
        else:
            if "price" in case:
                exp = case["price"]
                if exp is None and card.price is not None:
                    problems.append(f"price={card.price}, ждали null")
                elif exp is not None and (card.price is None or abs(card.price - exp) > 0.5):
                    problems.append(f"price={card.price}, ждали {exp}")
            if "currency" in case and card.currency != case["currency"]:
                problems.append(f"currency={card.currency}, ждали {case['currency']}")
            digits = case.get("contact_digits")
            if digits and digits not in "".join(ch for ch in (card.contact or "") if ch.isdigit()):
                problems.append(f"contact={card.contact!r}, ждали цифры {digits}")
            txt = case.get("contact_text")
            if txt and txt.lower() not in (card.contact or "").lower():
                problems.append(f"contact={card.contact!r}, ждали {txt}")
    else:
        if parsed.card is not None:
            problems.append("is_ad=false, но card не null")
    return (not problems, "; ".join(problems))


def check_classify(case: dict, parsed: ChannelClassification) -> tuple[bool, str]:
    problems: list[str] = []
    if parsed.channel_type != case["type"]:
        problems.append(f"type={parsed.channel_type.value}, ждали {case['type'].value}")
    if not parsed.languages or parsed.languages[0] != case["lang"]:
        got = [x.value for x in parsed.languages]
        problems.append(f"languages={got}, ждали первым {case['lang'].value}")
    return (not problems, "; ".join(problems))


async def run_one(
    client: AsyncOpenAI,
    state: ModelState,
    model: str,
    task: str,
    case: dict,
    sem: asyncio.Semaphore,
    args,
    prompts: dict[str, str],
) -> CallResult:
    res = CallResult(model=model, task=task, case=case["name"])
    if task == "extract":
        messages = [{"role": "system", "content": prompts["extract"]}, {"role": "user", "content": case["text"]}]
        schema, temperature = AdAnalysis, None  # как в проде: у analyze_ad temperature не задаётся
    else:
        user = build_channel_user_message(case["title"], case["about"], case["messages"])
        messages = [{"role": "system", "content": prompts["classify"]}, {"role": "user", "content": user}]
        schema, temperature = ChannelClassification, 0  # как в проде: temperature=0

    t0 = time.monotonic()
    try:
        async with sem:
            completion, variant = await call_model(
                client, state, model, messages=messages, schema=schema,
                temperature=temperature, max_tokens=args.max_tokens, require_parameters=args.require_parameters,
            )
        res.started = True
        res.latency_s = time.monotonic() - t0
        res.reasoning_variant = variant_label(variant)
        res.prompt_tokens, res.completion_tokens, res.reasoning_tokens, res.cost_usd = _usage_stats(completion)
        msg = completion.choices[0].message
        if msg.parsed is None:
            res.error = f"refusal/пустой ответ: {msg.refusal or msg.content!r}"[:300]
            return res
        res.schema_ok = True
        res.parsed = msg.parsed.model_dump(mode="json")
        res.correct, res.notes = (check_extract if task == "extract" else check_classify)(case, msg.parsed)
    except (ValidationError, LengthFinishReasonError) as e:
        res.started = True  # модель ответила, но JSON битый/усечённый
        res.latency_s = time.monotonic() - t0
        first_lines = " ".join(str(e).splitlines()[:2])
        res.error = f"битый/усечённый JSON: {type(e).__name__}: {first_lines[:160]}"
    except APIStatusError as e:
        res.latency_s = time.monotonic() - t0
        body = getattr(e, "body", None)
        res.error = f"HTTP {e.status_code}: {json.dumps(body, ensure_ascii=False)[:300] if body else str(e)[:300]}"
    except Exception as e:  # сеть, таймаут и т.п.
        res.latency_s = time.monotonic() - t0
        res.error = f"{type(e).__name__}: {str(e)[:300]}"
    return res


async def run_model(client: AsyncOpenAI, model: str, args, prompts: dict[str, str]) -> list[CallResult]:
    state = ModelState(MODELS.get(model, DEFAULT_LADDER))
    sem = asyncio.Semaphore(args.concurrency)
    results: list[CallResult] = []

    # Прогрев: по одному последовательному вызову на задачу — так лесенка reasoning/temperature
    # устаканивается один раз, а не гоняется параллельными запросами.
    warm = [("extract", AD_CASES[0]), ("classify", CHANNEL_CASES[0])]
    for task, case in warm:
        results.append(await run_one(client, state, model, task, case, sem, args, prompts))

    rest = [("extract", c) for c in AD_CASES[1:]] + [("classify", c) for c in CHANNEL_CASES[1:]]
    results += await asyncio.gather(*(run_one(client, state, model, t, c, sem, args, prompts) for t, c in rest))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Отчёт
# ──────────────────────────────────────────────────────────────────────────────
def summarize(model: str, rs: list[CallResult]) -> dict:
    n = len(rs)
    started = sum(r.started for r in rs)
    schema_ok = sum(r.schema_ok for r in rs)
    correct = sum(1 for r in rs if r.correct)
    reasoning = sum(r.reasoning_tokens for r in rs)
    costs = [r.cost_usd for r in rs if r.cost_usd is not None]
    lat = [r.latency_s for r in rs if r.started]
    variants = sorted({r.reasoning_variant for r in rs if r.reasoning_variant})
    first_err = next((r.error for r in rs if r.error), "")
    if schema_ok == n:
        verdict = "ЗАПУСКАЕТСЯ"
    elif schema_ok > 0:
        verdict = "НЕСТАБИЛЬНО"
    else:
        verdict = "НЕ ЗАПУСТИЛАСЬ"
    return {
        "model": model, "verdict": verdict, "calls": n, "started": started, "schema_ok": schema_ok,
        "correct": correct, "reasoning_tokens": reasoning,
        "cost_usd": round(sum(costs), 6) if costs else None,
        "avg_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
        "reasoning_variant": "; ".join(variants), "first_error": first_err,
    }


def print_report(all_results: dict[str, list[CallResult]], verbose: bool) -> list[dict]:
    summaries = [summarize(m, rs) for m, rs in all_results.items()]
    print("\n" + "=" * 100)
    print(f"{'модель':38} {'итог':15} {'JSON ок':8} {'верно':7} {'think-ток':10} {'$ за прогон':12} {'сек/вызов':9}")
    print("-" * 100)
    for s in summaries:
        cost = f"{s['cost_usd']:.5f}" if s["cost_usd"] is not None else "—"
        lat = f"{s['avg_latency_s']}" if s["avg_latency_s"] is not None else "—"
        print(
            f"{s['model']:38} {s['verdict']:15} {s['schema_ok']}/{s['calls']:<6} {s['correct']}/{s['calls']:<5} "
            f"{s['reasoning_tokens']:<10} {cost:12} {lat:9}"
        )
    print("=" * 100)
    for s in summaries:
        flags = []
        if s["reasoning_variant"]:
            flags.append(f"reasoning: {s['reasoning_variant']}")
        if s["reasoning_tokens"] > 0:
            flags.append("⚠ мышление НЕ отключилось (reasoning_tokens > 0)")
        if s["first_error"]:
            flags.append(f"ошибка: {s['first_error']}")
        if flags:
            print(f"• {s['model']}: " + " | ".join(flags))
    if verbose:
        print("\nНеверные ответы и ошибки:")
        for m, rs in all_results.items():
            for r in rs:
                if r.error or r.correct is False:
                    print(f"  [{m}] ({r.task}) {r.case}: {r.error or r.notes}")
    return summaries


async def main_async(args) -> int:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("Задайте OPENROUTER_API_KEY в окружении", file=sys.stderr)
        return 2
    llm_dir = Path(args.llm_dir)
    prompts = {
        "extract": load_system_prompt(llm_dir / "analyze.py"),
        "classify": load_system_prompt(llm_dir / "channel_classify.py"),
    }
    http_client = DefaultAsyncHttpxClient(proxy=args.proxy) if args.proxy else None
    client = AsyncOpenAI(
        api_key=key, base_url=args.base_url, max_retries=2, timeout=args.timeout, http_client=http_client
    )
    models = args.models or list(MODELS)
    print(f"Моделей: {len(models)}, вызовов на модель: {len(AD_CASES) + len(CHANNEL_CASES)} "
          f"({len(AD_CASES)} извлечение + {len(CHANNEL_CASES)} классификация)")
    per_model = await asyncio.gather(*(run_model(client, m, args, prompts) for m in models))
    all_results = dict(zip(models, per_model))
    summaries = print_report(all_results, args.verbose)

    out = Path(args.out)
    out.write_text(
        json.dumps(
            {"summary": summaries, "calls": [asdict(r) for rs in all_results.values() for r in rs]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nПолный отчёт (с ответами моделей): {out}")
    return 0 if all(s["verdict"] == "ЗАПУСКАЕТСЯ" for s in summaries) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm-dir", default="./llm", help="папка с analyze.py и channel_classify.py")
    ap.add_argument("--models", nargs="*", help="ID моделей OpenRouter (по умолчанию — список в скрипте)")
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--proxy", default=None, help="HTTP-прокси, как settings.PROXY в проекте")
    ap.add_argument("--require-parameters", action="store_true",
                    help='добавить provider.require_parameters=true (только провайдеры с поддержкой response_format)')
    ap.add_argument("--concurrency", type=int, default=4, help="одновременных запросов на модель")
    ap.add_argument("--max-tokens", type=int, default=1500, help="лимит выходных токенов (защита от «ушедшего в раздумья»)")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--out", default="smoke_results.json")
    ap.add_argument("--verbose", action="store_true", help="печатать каждый неверный ответ и ошибку")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
