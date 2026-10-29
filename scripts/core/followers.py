"""Число подписчиков профиля: из og:description страницы и, запасным путём, из JSON страницы."""

import re

# «1.2K Followers», «12,345 Followers», «5.5M Followers», «12 тыс. подписчиков»
_OG = re.compile(
    r"(\d[\d\s.,  ]*?)\s*(тыс\.?|млн\.?|[KkMmКкМм])?\s*(?:followers?|подписчик\w*)",
    re.I,
)
_MULT = {"k": 1_000, "к": 1_000, "тыс": 1_000, "m": 1_000_000, "м": 1_000_000, "млн": 1_000_000}
_JSON_WINDOW = 1500


def _to_int(num: str, mult: str | None) -> int | None:
    num = num.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not num:
        return None
    try:
        if mult:
            m = _MULT.get(mult.lower().rstrip("."))
            return int(round(float(num.replace(",", ".")) * m)) if m else None
        return int(re.sub(r"[.,]", "", num))  # «1,234» и «1.234» = тысячные разделители
    except ValueError:
        return None


def followers_from_og(text: str | None) -> int | None:
    """Подписчики из og:description; None, если число не найдено."""
    if not text:
        return None
    m = _OG.search(text)
    return _to_int(m.group(1), m.group(2)) if m else None


def followers_from_json(scripts: list[str], username: str) -> int | None:
    """Запасной путь: "follower_count" в JSON страницы рядом с "username":"<ник>".

    Ищем только ПОСЛЕ его username и до следующего чужого username, чтобы не взять число
    рекомендованного профиля. Нет надёжного совпадения: None (канал не освобождается от проверки)."""
    mark = f'"username":"{username}"'
    other = '"username":"'
    for txt in scripts:
        i = txt.lower().find(mark.lower())
        while i != -1:
            tail = txt[i + len(mark) : i + len(mark) + _JSON_WINDOW]
            cut = tail.find(other)
            chunk = tail if cut == -1 else tail[:cut]
            m = re.search(r'"follower_count"\s*:\s*(\d+)', chunk)
            if m:
                return int(m.group(1))
            i = txt.lower().find(mark.lower(), i + 1)
    return None
