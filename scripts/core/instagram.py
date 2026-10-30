"""Поиск связанного Instagram-аккаунта по данным профиля Threads.

Threads — продукт Meta, большинство аккаунтов заведены через привязанный Instagram.
Страница профиля часто отдаёт это прямо в embedded JSON (ключи вида instagram_hint,
cross_post_... и т.п.; Meta меняет их названия без предупреждения, поэтому ищем по
набору известных ключей, а не по одному). Если ничего не нашли — используем ник
Threads как предположение (Instagram и Threads почти всегда используют один ник),
но это ДОГАДКА и помечается как неподтверждённая (confidence='guessed').
"""

import re
from dataclasses import dataclass

from core.jsonwalk import walk

DETECTOR_VERSION = 1

# Ключи в JSON-документах страницы, где встречается связанный IG-ник.
# Список заведомо неполный — Threads отдаёт разные поля в разных версиях API;
# расширяется по мере наблюдений за реальным трафиком (см. README шага 4).
IG_KEYS = (
    "instagram_hint",
    "ig_username",
    "cross_post_username",
    "linked_instagram_username",
    "instagram_username",
)

_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$", re.IGNORECASE)

# instagram.com/<ник> в тексте биографии/внешней ссылки
_BIO_LINK_RE = re.compile(r"(?:instagram\.com|instagr\.am)/([a-z0-9._]{1,30})/?", re.IGNORECASE)

# Первые сегменты путей instagram.com, которые не ник (ссылка на пост, рилс и т.п.)
RESERVED_PATHS = frozenset({"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct"})


@dataclass(frozen=True)
class IgLink:
    username: str
    source: str  # 'header_link' | 'threads_json' | 'username_guess'
    confidence: str  # 'confirmed' | 'guessed'
    matched_raw: str | None


def _valid(username: str | None) -> str | None:
    if not username:
        return None
    username = username.strip().lstrip("@")
    return username if _USERNAME_RE.match(username) else None


def find_in_json(docs, owner: str | None = None) -> IgLink | None:
    """Ищет IG-ник в embedded JSON-документах страницы (объекты из jsonwalk.walk).

    `docs` — объект верхнего уровня (dict/list), как возвращает read_embedded;
    функция сама обходит вложенность. owner — ник профиля в Threads: в JSON страницы
    лежат и рекомендованные профили, поэтому берём только узел, где рядом с IG-ключом
    стоит "username" этого же профиля (иначе можно приписать чужой Instagram)."""
    for node in walk(docs):
        if owner is not None and str(node.get("username", "")).lower() != owner.lower():
            continue
        for key in IG_KEYS:
            if key in node:
                uname = _valid(node.get(key))
                if uname:
                    return IgLink(
                        username=uname,
                        source="threads_json",
                        confidence="confirmed",
                        matched_raw=f"{key}={node[key]!r}",
                    )
    return None


def find_in_bio(bio: str | None) -> IgLink | None:
    """Запасной путь: ссылка на instagram.com/<ник> в тексте биографии."""
    if not bio:
        return None
    m = _BIO_LINK_RE.search(bio)
    if m is None:
        return None
    uname = _valid(m.group(1))
    if uname is None or uname.lower() in RESERVED_PATHS:
        return None
    return IgLink(
        username=uname, source="threads_json", confidence="confirmed", matched_raw=m.group(0)
    )


def guess_by_username(threads_username: str) -> IgLink:
    """Непроверенное предположение: тот же ник, что и в Threads."""
    return IgLink(
        username=threads_username,
        source="username_guess",
        confidence="guessed",
        matched_raw=None,
    )


def find_instagram_link(docs, bio: str | None, threads_username: str) -> IgLink:
    """JSON страницы (узел этого профиля) -> ссылка в био -> угадывание по нику.

    Ссылка/иконка Instagram в шапке (самый надёжный источник) обрабатывается раньше:
    см. scrape/instagram.py::resolve_instagram."""
    return (
        find_in_json(docs, owner=threads_username)
        or find_in_bio(bio)
        or guess_by_username(threads_username)
    )
