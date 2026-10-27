"""Фильтр «олх» по тексту постов."""

import re
from pathlib import Path

from core.fsio import read_jsonl, write_jsonl_atomic

# ---------------------------------------------------------------------------
# Базовая фильтрация: разложить собранные посты на approved и rejected.
# Критерий: в тексте поста или его теме/заголовке есть olx / олх.
# ---------------------------------------------------------------------------

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


def post_matches_filter(post: dict) -> bool:
    """Есть ли в тексте поста или его теме/заголовке ключевые слова.

    Поле запроса (query) намеренно не проверяется: иначе все посты по
    запросу «олх…» автоматически попадали бы в approved, даже если сам
    пост не про олх.
    """
    blob = normalize_text((post.get("text") or "") + "\n" + (post.get("topic") or ""))
    return bool(FILTER_RE.search(blob))


def filter_posts_file(input_path: Path, approved_path: Path, rejected_path: Path) -> dict:
    """Проходит по posts.jsonl и раскладывает посты на два файла.

    Возвращает статистику: {"total", "approved", "rejected"}.
    Идемпотентно: повторный запуск перезаписывает выходные файлы.
    """
    posts = read_jsonl(input_path)

    approved: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()

    for p in posts:
        if not isinstance(p, dict):
            continue

        # дедуп по code (posts.jsonl и так без дублей, но защитимся)
        code = p.get("code")
        if code and code in seen:
            continue
        if code:
            seen.add(code)

        if post_matches_filter(p):
            approved.append({**p, "filter_status": "approved"})
        else:
            rejected.append({**p, "filter_status": "rejected"})

    write_jsonl_atomic(approved_path, approved)
    write_jsonl_atomic(rejected_path, rejected)

    return {
        "total": len(approved) + len(rejected),
        "approved": len(approved),
        "rejected": len(rejected),
    }
