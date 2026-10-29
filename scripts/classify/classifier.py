"""classify_profile: контекст профиля -> категория. Без БД и браузера.

Неизвестный код категории или невалидный JSON не бросают исключение: результат `other`
с пометкой error (профиль потом получит review_failed). Ошибки HTTP бросает client.
"""

import json
import re
from dataclasses import dataclass, field

from classify.client import LLMClient
from classify.prompts import PROMPT_VERSION, build_system_prompt, build_user_prompt

FALLBACK = "other"


@dataclass
class ClassifyResult:
    category: str
    confidence: float | None = None
    reason: str | None = None
    error: str | None = None  # не пусто = ответ модели не разобран / код неизвестен
    raw: dict = field(default_factory=dict)  # то, что пишется в profiles.category_raw


def parse_answer(text: str, valid_codes: set[str]) -> ClassifyResult:
    """JSON из ответа модели (допускаются ```-ограждения и текст вокруг)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return ClassifyResult(FALLBACK, error="невалидный JSON в ответе модели")
    code = str(data.get("category") or "").strip().lower()
    reason = data.get("reason") if isinstance(data.get("reason"), str) else None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        conf = None
    if code not in valid_codes:
        return ClassifyResult(FALLBACK, conf, reason, error=f"неизвестная категория «{code}»")
    return ClassifyResult(code, conf, reason)


async def classify_profile(
    client: LLMClient,
    categories: list[tuple[str, str, str | None]],
    profile: dict,
    sample_threads: list[str],
) -> ClassifyResult:
    """categories: активные (code, name_ru, description); в них обязан быть `other`."""
    resp = await client.complete(
        build_system_prompt(categories), build_user_prompt(profile, sample_threads)
    )
    result = parse_answer(resp.text, {c[0] for c in categories})
    result.raw = {
        "category": result.category,
        "confidence": result.confidence,
        "reason": result.reason,
        "error": result.error,
        "answer": resp.text[:1000],
        "latency_ms": resp.latency_ms,
        "requests": resp.requests,
        "prompt_tokens": resp.prompt_tokens,
        "completion_tokens": resp.completion_tokens,
        "prompt_version": PROMPT_VERSION,
    }
    return result
