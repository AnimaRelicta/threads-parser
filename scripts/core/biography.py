"""Биография профиля из JSON страницы Threads.

Та же эвристика, что в core/followers.py (followers_from_json): Threads не отдаёт
её отдельным meta-тегом, поэтому ищем `"biography":"..."` в сыром JSON страницы,
в окне сразу ПОСЛЕ `"username":"<ник>"`, чтобы не зацепить био рекомендованного
профиля из того же документа.

ВАЖНО: точная раскладка полей в JSON Threads не документирована и может отличаться
от того, что было на момент написания (см. README шага 2 в патче) — функция рассчитана
на подтверждение/правку по дампу реальной страницы через debug_dump_profile_json.
"""

import re

from core.textfix import decode_json_body

_BIO_RE = re.compile(r'"biography"\s*:\s*"((?:[^"\\]|\\.)*)"')
_JSON_WINDOW = 1500


def _unescape(s: str) -> str:
    """Тело JSON-строки -> текст. Прежняя перекодировка через latin1 ломалась на `\\uXXXX`
    (кириллица и эмодзи) и возвращала строку как есть, с видимыми `\\u041f...`."""
    return decode_json_body(s)


def biography_from_json(scripts: list[str], username: str) -> str | None:
    """Био из JSON страницы; None, если не нашли рядом с нужным username."""
    mark = f'"username":"{username}"'
    other = '"username":"'
    for txt in scripts:
        i = txt.lower().find(mark.lower())
        while i != -1:
            tail = txt[i + len(mark) : i + len(mark) + _JSON_WINDOW]
            cut = tail.find(other)
            chunk = tail if cut == -1 else tail[:cut]
            m = _BIO_RE.search(chunk)
            if m:
                bio = _unescape(m.group(1)).strip()
                return bio or None
            i = txt.lower().find(mark.lower(), i + 1)
    return None
