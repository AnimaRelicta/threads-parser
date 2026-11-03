"""Починка текста, записанного в БД с неразобранными JSON-escape (`\u041f\u043e...`).

Био берётся из JSON страницы Threads (core/biography.py). В JSON кириллица и эмодзи могут стоять
как `\\uXXXX` (эмодзи — суррогатной парой `\\ud83d\\udcd1`). Раньше такая строка из-за ошибки
перекодировки попадала в БД как есть. Здесь тело JSON-строки превращается в обычный текст.
"""

import json
import re

JSON_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")


def has_json_escapes(text: str | None) -> bool:
    """В строке есть последовательности `\\uXXXX` (признак неразобранного JSON)."""
    return bool(text) and JSON_ESCAPE.search(text) is not None


def _pairs(s: str) -> str:
    # Одиночные половины суррогатной пары склеиваются в символ; битые заменяются на «�»
    return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def decode_json_body(body: str) -> str:
    """Тело JSON-строки (без кавычек) -> текст: `\\uXXXX`, суррогатные пары, `\\n`, `\\\"`, `\\/`."""
    try:
        return _pairs(json.loads(f'"{body}"', strict=False))
    except ValueError:  # в теле остался голый `"` или неизвестный escape: хотя бы \uXXXX
        return _pairs(JSON_ESCAPE.sub(lambda m: chr(int(m.group(0)[2:], 16)), body))


def fix_text(text: str | None) -> str | None:
    """Строка из БД без `\\uXXXX`; текст без escape возвращается как есть."""
    return decode_json_body(text).strip() if has_json_escapes(text) else text
