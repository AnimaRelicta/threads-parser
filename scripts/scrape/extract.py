"""Разбор JSON-документов Threads: обход, медиа, текст поста."""

import json

from playwright.async_api import Error as PlaywrightError


def media_urls(m: dict) -> list[tuple[str, str]]:
    """Список (тип, url) для поста: одиночного или карусели. Может быть пустым."""
    nodes = m["carousel_media"] if m.get("carousel_media") else [m]
    out = []
    for n in nodes:
        kind, url = "image", None
        if n.get("video_versions"):
            kind, url = "video", n["video_versions"][0].get("url")
        elif n.get("image_versions2"):
            cands = n["image_versions2"].get("candidates") or []
            if cands:
                url = cands[0].get("url")
        if url:
            out.append((kind, url))
    return out


def caption_of(m: dict) -> str | None:
    c = m.get("caption")
    if isinstance(c, dict):
        return c.get("text")
    if isinstance(c, str):
        return c
    return None


def is_video_obj(d: dict) -> bool:
    items = media_urls(d)
    return bool(d.get("media_type") == 2 or (items and all(k == "video" for k, _ in items)))


async def read_embedded(page) -> list:
    """JSON, встроенный в HTML страницы (читаются только нужные скрипты)."""
    docs: list = []
    try:
        scripts = await page.eval_on_selector_all(
            'script[type="application/json"]', "els => els.map(e => e.textContent)"
        )
    except PlaywrightError:
        return docs
    for txt in scripts:
        if not txt or ('"taken_at"' not in txt and '"caption"' not in txt):
            continue
        try:
            docs.append(json.loads(txt))
        except (json.JSONDecodeError, TypeError):
            pass
    return docs
