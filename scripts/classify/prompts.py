"""Промпты классификации профиля: системный (категории из БД) и пользовательский (профиль + ветки).

Ответ модели строго JSON: {"category": "vitrina", "confidence": 0.8, "reason": "..."}.
При любой правке текста промпта поднимайте PROMPT_VERSION (profiles.category_prompt_version).
"""

PROMPT_VERSION = 1
MAX_THREAD_CHARS = 500


def build_system_prompt(categories: list[tuple[str, str, str | None]]) -> str:
    """categories: (code, name_ru, description) только активных категорий."""
    lines = [f'- "{code}": {name}. {desc or ""}'.rstrip() for code, name, desc in categories]
    return (
        "Ты классифицируешь аккаунт (профиль) в Threads по его описанию и последним веткам "
        "(записям верхнего уровня). Выбери ровно одну категорию из списка:\n"
        + "\n".join(lines)
        + '\n\nОтветь ТОЛЬКО JSON-объектом без пояснений и без markdown: '
        '{"category": "<код из списка>", "confidence": <число от 0 до 1>, '
        '"reason": "<одно короткое предложение>"}. '
        'Если данных мало или ничего не подходит, выбери "other".'
    )


def build_user_prompt(profile: dict, threads: list[str]) -> str:
    parts = [
        f"Профиль: @{profile['username']}",
        f"Название: {profile.get('og_title') or '-'}",
        f"Описание: {profile.get('og_description') or '-'}",
    ]
    if threads:
        parts.append("Последние ветки:")
        parts += [f"{i}. {t[:MAX_THREAD_CHARS]}" for i, t in enumerate(threads, 1)]
    else:
        parts.append("Веток нет.")
    return "\n".join(parts)
