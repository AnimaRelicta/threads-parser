"""Разбор ленты профиля Instagram без браузера: страница ответа -> лёгкие поля постов.

Форматы ответов Instagram меняются без предупреждения, поэтому поля читаются по набору
известных имён. ПРОВЕРИТЬ НА ЖИВОЙ СЕССИИ: pinned, views, comments_disabled."""

import re
from datetime import datetime, timezone

from config import IG

_HASHTAG = re.compile(r"#(\w+)")
_MENTION = re.compile(r"@([A-Za-z0-9._]+)")
_LOGIN_MESSAGES = ("login_required", "checkpoint_required", "consent_required")
_RATE_MARKERS = ("wait a few minutes", "rate limit", "too many")


def classify_error(res) -> str | None:
    """Ответ JS_FETCH без данных -> rate | auth | forbidden | not_found | error; None — данные есть.

    403 отдельно от 401: так бывает у закрытого профиля, это не обязательно проблема сессии."""
    if not isinstance(res, dict):
        return "error"
    if "error" in res:
        return {429: "rate", 401: "auth", 403: "forbidden", 404: "not_found"}.get(
            res["error"], "error"
        )
    if res.get("status") == "fail":
        msg = str(res.get("message") or "").lower()
        if any(m in msg for m in _RATE_MARKERS):
            return "rate"
        if any(m in msg for m in _LOGIN_MESSAGES):
            return "auth"
        return "error"
    return None


def classify_feed(res) -> tuple[str, dict | None]:
    """Ответ ленты -> (вид, страница): page | rate | auth | forbidden | not_found | error.

    Страница: {"items": [...], "next": курсор или None, "more": bool}."""
    bad = classify_error(res)
    if bad:
        return bad, None
    items = res.get("items")
    if not isinstance(items, list):
        return "error", None
    nxt = res.get("next_max_id")
    return "page", {
        "items": [i for i in items if isinstance(i, dict)],
        "next": str(nxt) if nxt else None,
        "more": bool(res.get("more_available")),
    }


def _kind(item: dict) -> str | None:
    mt = item.get("media_type")
    if mt == 8:
        return "carousel"
    if mt == 2:
        return "reel" if item.get("product_type") == "clips" else "video"
    if mt == 1:
        return "photo"
    return None


def _tagged(item: dict) -> list[str]:
    nodes = [item, *(item.get("carousel_media") or [])]
    out: list[str] = []
    for n in nodes:
        for t in ((n.get("usertags") or {}).get("in")) or []:
            nick = (t.get("user") or {}).get("username")
            if nick and nick not in out:
                out.append(nick)
    return out


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _location(item: dict) -> tuple[str | None, dict | None]:
    loc = item.get("location")
    if not isinstance(loc, dict) or not loc.get("name"):
        return None, None
    keep = ("pk", "name", "short_name", "city", "address", "lat", "lng")
    return loc["name"], {k: loc[k] for k in keep if loc.get(k) is not None}


def post_from_item(item: dict) -> dict | None:
    """Элемент ленты -> поля таблицы posts (без комментариев и медиа). None — не пост."""
    code = item.get("code")
    if not code:
        return None
    kind = _kind(item)
    caption = (item.get("caption") or {}).get("text") if isinstance(item.get("caption"), dict) else None
    ts = item.get("taken_at")
    taken = datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None
    hidden = bool(item.get("like_and_view_counts_disabled"))
    views = next(
        (item[k] for k in ("play_count", "ig_play_count", "view_count") if item.get(k) is not None),
        None,
    )
    pk = item.get("pk") or str(item.get("id") or "").split("_")[0] or None
    loc_name, loc = _location(item)
    size = item.get("carousel_media_count") or len(item.get("carousel_media") or []) or None
    return {
        "code": code,
        "media_id": str(pk) if pk else None,
        "url": f"{IG}/{'reel' if kind == 'reel' else 'p'}/{code}/",
        "kind": kind,
        "caption": caption,
        "taken_at": taken,
        "likes": None if hidden else item.get("like_count"),
        "comments_count": item.get("comment_count"),
        "views": views,
        "hashtags": _uniq([h.lower() for h in _HASHTAG.findall(caption or "")]),
        "mentions": _uniq([m.lower() for m in _MENTION.findall(caption or "")]),
        "tagged_users": _tagged(item),
        "location_name": loc_name,
        "location": loc,
        "is_pinned": bool(item.get("timeline_pin_info") or item.get("is_pinned")),
        "comments_disabled": bool(
            item.get("comments_disabled") or item.get("commenting_disabled_for_viewer")
        ),
        "carousel_size": size,
    }


def _best(candidates) -> dict | None:
    """Самый большой кандидат из image_versions2.candidates / video_versions."""
    cands = [c for c in candidates or [] if isinstance(c, dict) and c.get("url")]
    return max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0), default=None)


def media_slides(item: dict) -> list[dict]:
    """Фото и видео поста по слайдам: [{position, kind, url, width, height, duration}].

    kind image — фото (у видео-слайда это обложка, её отдельно не качаем); kind video — видео."""
    nodes = item.get("carousel_media") or [item]
    out = []
    for i, n in enumerate(nodes, 1):
        video = _best(n.get("video_versions"))
        pic = _best((n.get("image_versions2") or {}).get("candidates"))
        src = video if (n.get("media_type") == 2 or video) and video else pic
        if not src:
            continue
        out.append(
            {
                "position": i,
                "kind": "video" if src is video else "image",
                "url": src["url"],
                "width": src.get("width"),
                "height": src.get("height"),
                "duration": n.get("video_duration") if src is video else None,
            }
        )
    return out


def comment_from_dict(c: dict, post_code: str, parent_id: str | None) -> dict | None:
    """Комментарий или ответ -> строка comments (depth 1 или 2)."""
    cid = c.get("pk") or c.get("id")
    if not cid:
        return None
    user = c.get("user") if isinstance(c.get("user"), dict) else {}
    ts = c.get("created_at_utc") or c.get("created_at")
    return {
        "id": str(cid),
        "post_code": post_code,
        "parent_id": parent_id,
        "depth": 2 if parent_id else 1,
        "author_username": user.get("username"),
        "author_id": str(user["pk"]) if user.get("pk") else None,
        "text": c.get("text"),
        "created_at": datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if isinstance(ts, (int, float))
        else None,
        "likes": c.get("comment_like_count"),
        "reply_count": c.get("child_comment_count") or 0,
    }
