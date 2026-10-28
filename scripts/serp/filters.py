"""Фильтр «олх» по тексту постов."""

from pathlib import Path

from core.fsio import read_jsonl, write_jsonl_atomic
from core.textfilter import classify


def post_matches_filter(post: dict) -> bool:
    """Есть ли в тексте поста или его теме/заголовке ключевые слова."""
    return classify(post.get("text"), post.get("topic"))[0] == "approved"


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
