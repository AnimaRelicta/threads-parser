"""Извлечение постов и тем из ответов поиска."""

import json

from playwright.async_api import Error as PlaywrightError

from browser.session import USER_ID_KEYS
from config import TH
from core.jsonwalk import walk
from core.timeutil import iso
from scrape.extract import caption_of, topic_of


async def read_all_embedded(page) -> list:
    """Все встроенные JSON страницы, без фильтра по полям (в отличие от
    read_embedded из functions.py, который берёт только документы с
    taken_at/caption — для /@user). Структура выдачи поиска ещё не известна,
    поэтому на разведке берём всё, а не гадаем, какие поля важны."""

    docs: list = []
    try:
        scripts = await page.eval_on_selector_all(
            'script[type="application/json"]', "els => els.map(e => e.textContent)"
        )
    except PlaywrightError:
        return docs
    for txt in scripts:
        if not txt:
            continue
        try:
            docs.append(json.loads(txt))
        except (json.JSONDecodeError, TypeError):
            pass
    return docs


def find_paths(obj, needle: str, path: str = "$", limit: int = 50) -> list[tuple[str, str]]:
    """Все пути до строковых значений, содержащих needle (без учёта регистра).

    Путь в нотации $.a.b[3].c — так его удобно сверять руками и потом
    использовать для извлечения поля (шаг 3).
    """
    needle = needle.lower()
    out: list[tuple[str, str]] = []

    def walk_path(x, p: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                walk_path(v, f"{p}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk_path(v, f"{p}[{i}]")
        elif isinstance(x, str) and needle in x.lower():
            out.append((p, x if len(x) <= 200 else x[:200] + "…"))

    walk_path(obj, path)
    return out


# Ключи, которые дальше не интересны при печати схемы поста (видны уже и так,
# или это огромные подструктуры, не относящиеся к поиску темы)
NOISY_KEYS = {"video_versions", "image_versions2", "carousel_media"}


def harvest_post_pool(docs: list) -> dict[str, dict]:
    """code -> самый полный объект поста среди всех документов (walk из
    functions.py уже делает это для /@user; здесь то же самое, но по всей
    пачке документов разведки сразу, без Harvester с его «порядком»)."""

    pool: dict[str, dict] = {}
    for doc in docs:
        for d in walk(doc):
            code = d.get("code")
            if code and "taken_at" in d:
                prev = pool.get(code)
                if prev is None or len(d) > len(prev):
                    pool[code] = d
    return pool


def doc_is_useful(doc) -> bool:
    """Документ стоит сохранять: в нём есть объект поста (code+taken_at)
    или searchResults/tag_info (структура выдачи). Отсеивает
    webpack-боилерплейт (require[]-деревья, которые только эхом повторяют
    query) и повторные route-экспорты для той же страницы."""

    for d in walk(doc):
        if ("code" in d and "taken_at" in d) or "searchResults" in d or "tag_info" in d:
            return True
    return False


def caption_text(d: dict) -> str | None:
    c = caption_of(d)
    return c if c is None or len(c) <= 80 else c[:80] + "…"


def extract_post(d: dict, query: str, mode: str) -> dict | None:
    """Пост -> плоский словарь для сохранения. None, если нет даже кода/автора
    (битый объект — такое бывает среди «мусорных» объектов, похожих на пост)."""

    code = d.get("code")
    user = d.get("user") if isinstance(d.get("user"), dict) else {}
    username = user.get("username")
    if not code or not username:
        return None
    user_id = next((str(user[k]) for k in USER_ID_KEYS if user.get(k) is not None), None)
    tpai = d.get("text_post_app_info") or {}
    return {
        "code": code,
        "url": f"{TH}/@{username}/post/{code}",
        "username": username,
        "user_id": user_id,
        "text": caption_of(d),
        "topic": topic_of(d),
        "date_utc": iso(d.get("taken_at")),
        "likes": d.get("like_count"),
        "reply_count": tpai.get("direct_reply_count"),
        "query": query,
        "mode": mode,
    }


def iter_search_posts(docs: list, query: str, mode: str) -> list[dict]:
    """Все посты из пачки документов разведки/выдачи одной задачи, уже
    извлечённые extract_post (без дедупликации между задачами — это шаг 4)."""
    pool = harvest_post_pool(docs)
    out = []
    for d in pool.values():
        p = extract_post(d, query, mode)
        if p is not None:
            out.append(p)
    return out
