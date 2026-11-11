"""Разбор данных профиля Instagram без браузера: ответ API, embedded JSON, og:*.

Форматы ответов Instagram меняются без предупреждения, поэтому поля читаются по набору
известных имён (API веб-клиента и старый graphql-вид), а не по одному.
"""

import re

from core.jsonwalk import walk

PROFILE_KEYS = ("biography", "follower_count", "edge_followed_by", "full_name")
PRIVATE_MARKERS = ("this account is private", "аккаунт закрыт", "закрытый аккаунт")
_FOLLOWERS = (
    r"([\d.,\s\xa0]+[KkMmКкМм]?)\s*(?:Followers?|подписчик\w*)",
    r"([\d.,\s\xa0]+[KkMmКкМм]?)\s*(?:Following|подписк\w*)",
    r"([\d.,\s\xa0]+[KkMmКкМм]?)\s*(?:Posts?|публикаци\w*)",
)
_OG_TITLE = re.compile(r"^(.*?)\s*\(@([A-Za-z0-9._]{1,30})\)")
_OG_BIO = re.compile(r"(?:on Instagram|в Instagram):\s*[\"“«](.*)[\"”»]\s*$", re.DOTALL)


def _first(*vals):
    return next((v for v in vals if v is not None), None)


def _count(v) -> int | None:
    """Число из int или {'count': n} (edge_followed_by и т. п.)."""
    if isinstance(v, dict):
        v = v.get("count")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def user_from_dict(u: dict) -> dict:
    """Объект пользователя (API веб-профиля, users/{id}/info, embedded) -> поля profiles."""
    uid = _first(u.get("id"), u.get("pk"), u.get("pk_id"))
    links = u.get("bio_links")
    if isinstance(links, list):
        links = [
            {"url": x.get("url"), "title": x.get("title") or None}
            for x in links
            if isinstance(x, dict) and x.get("url")
        ]
    else:
        links = None
    return {
        "ig_user_id": str(uid) if uid is not None else None,
        "username": u.get("username"),
        "full_name": u.get("full_name"),
        "biography": u.get("biography"),
        "bio_links": links,
        "external_url": u.get("external_url") or None,
        "category": _first(u.get("category_name"), u.get("category")) or None,
        "followers_count": _first(
            _count(u.get("edge_followed_by")), _count(u.get("follower_count"))
        ),
        "following_count": _first(_count(u.get("edge_follow")), _count(u.get("following_count"))),
        "posts_count": _first(
            _count(u.get("edge_owner_to_timeline_media")), _count(u.get("media_count"))
        ),
        "is_private": u.get("is_private"),
        "is_verified": u.get("is_verified"),
        "is_business": _first(u.get("is_business_account"), u.get("is_business")),
        "profile_pic_url": _first(u.get("profile_pic_url_hd"), u.get("profile_pic_url")),
    }


def classify_api(res) -> tuple[str, dict | None]:
    """Ответ JS_FETCH -> (вид, пользователь): user | not_found | rate | auth | unknown."""
    if not isinstance(res, dict):
        return "unknown", None
    if "error" in res:
        code = res["error"]
        if code == 429:
            return "rate", None
        if code in (401, 403):
            return "auth", None  # ПРОВЕРИТЬ: Instagram отдаёт 401 и при лимите запросов
        if code == 404:
            return "not_found", None
        return "unknown", None
    data = res.get("data")
    if isinstance(data, dict) and "user" in data:
        user = data["user"]
        return ("user", user) if isinstance(user, dict) else ("not_found", None)
    if isinstance(res.get("user"), dict):  # users/{id}/info
        return "user", res["user"]
    return "unknown", None


def parse_count(raw: str) -> int | None:
    """'1,234' -> 1234; '1.2M' -> 1200000; '12 345' -> 12345."""
    s = re.sub(r"[\s\xa0]", "", raw or "")
    if not s:
        return None
    mult = {"k": 10**3, "к": 10**3, "m": 10**6, "м": 10**6}.get(s[-1].lower())
    try:
        if mult:
            return int(float(s[:-1].replace(",", ".")) * mult)
        return int(re.sub(r"[.,]", "", s))
    except ValueError:
        return None


def meta_from_og(og_title: str | None, og_description: str | None) -> dict:
    """Запасной путь: og:title и og:description (подписчики, подписки, посты, имя, био)."""
    meta: dict = {}
    desc = og_description or ""
    for key, pat in zip(("followers_count", "following_count", "posts_count"), _FOLLOWERS):
        m = re.search(pat, desc, re.IGNORECASE)
        if m:
            meta[key] = parse_count(m.group(1))
    t = _OG_TITLE.match((og_title or "").strip())
    if t and t.group(1):
        meta["full_name"] = t.group(1).strip()
    b = _OG_BIO.search(desc)
    if b:
        meta["biography"] = b.group(1).strip()
    return {k: v for k, v in meta.items() if v is not None}


def find_embedded_user(docs: list, username: str) -> dict | None:
    """Объект этого профиля среди embedded JSON страницы (самый полный по числу ключей)."""
    best = None
    for doc in docs:
        for d in walk(doc):
            if str(d.get("username", "")).lower() != username.lower():
                continue
            if any(k in d for k in PROFILE_KEYS) and (best is None or len(d) > len(best)):
                best = d
    return best


def private_from_text(text: str | None) -> bool:
    """Закрытый профиль по тексту страницы (только когда API не ответил)."""
    low = (text or "").lower()
    return any(m in low for m in PRIVATE_MARKERS)
