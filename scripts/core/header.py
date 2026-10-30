"""Шапка профиля Threads: темы, ссылки в био, «недавние просмотры».

Чистый разбор данных, снятых со страницы (scrape/header.py): без браузера и БД.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from core.followers import _to_int
from core.instagram import RESERVED_PATHS

# «38 621 недавних просмотров», «1,2 тыс. недавних просмотров», «12K recent views»
_VIEWS = re.compile(
    r"(\d[\d\s.,   ]*?)\s*(тыс\.?|млн\.?|[KkMmКкМм])?\s*"
    r"(?:недавн\w*\s+просмотр\w*|recent\s+views?)",
    re.I,
)
_TRACKING = ("utm_", "fbclid", "igshid", "igsh")


def parse_views(text: str | None) -> int | None:
    """Число недавних просмотров из текста кнопки шапки; None, если не нашли."""
    m = _VIEWS.search(text or "")
    return _to_int(m.group(1), m.group(2)) if m else None


def clean_url(href: str) -> str:
    """Ссылка без трекинга (utm_*, fbclid); редирект l.threads.com/?u=... раскрывается."""
    u = urlsplit(href)
    if u.hostname and u.hostname.lower().startswith("l.threads."):
        target = parse_qs(u.query).get("u")
        if target:
            u = urlsplit(target[0])
    q = [
        (k, v)
        for k, v in parse_qsl(u.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING)
    ]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def classify_link(url: str) -> dict:
    """{kind: instagram|telegram|whatsapp|other, handle}: ник или номер телефона из ссылки."""
    u = urlsplit(url)
    host = (u.hostname or "").lower().removeprefix("www.")
    first = next((p for p in u.path.split("/") if p), "")
    if host in ("instagram.com", "instagr.am"):
        handle = None if first.lower() in RESERVED_PATHS else first or None
        return {"kind": "instagram", "handle": handle}
    if host in ("t.me", "telegram.me"):
        return {"kind": "telegram", "handle": first.lstrip("@") or None}
    if host in ("wa.me", "api.whatsapp.com", "whatsapp.com"):
        phone = re.sub(r"\D", "", first or parse_qs(u.query).get("phone", [""])[0])
        return {"kind": "whatsapp", "handle": phone or None}
    return {"kind": "other", "handle": None}


@dataclass
class Header:
    recent_views: int | None = None
    topics: list[dict] = field(default_factory=list)  # [{text, query, tag_id}]
    links: list[dict] = field(default_factory=list)  # [{text, url, kind, handle}]

    def __bool__(self) -> bool:
        return self.recent_views is not None or bool(self.topics) or bool(self.links)

    @property
    def instagram(self) -> dict | None:
        """Первая ссылка на Instagram (иконка в шапке или ссылка в био)."""
        return next((x for x in self.links if x["kind"] == "instagram" and x["handle"]), None)


def parse_header(raw: dict) -> Header:
    """Сырой результат READ_HEADER_JS -> Header."""
    h = Header(recent_views=parse_views(raw.get("views")))
    seen: set[str] = set()
    for t in raw.get("topics") or []:
        qs = parse_qs(urlsplit(t["href"]).query)
        tag_id = (qs.get("tag_id") or [None])[0]
        key = tag_id or t["text"]
        if not t["text"] or key in seen:
            continue
        seen.add(key)
        h.topics.append({"text": t["text"], "query": (qs.get("q") or [None])[0], "tag_id": tag_id})
    seen.clear()
    for a in raw.get("links") or []:
        url = clean_url(a["href"])
        if url in seen:
            continue
        seen.add(url)
        text = a.get("text") or a.get("icon") or url
        h.links.append({"text": text, "url": url, **classify_link(url)})
    return h
