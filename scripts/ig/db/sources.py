"""Источники профилей Instagram: нормализация ника, разбор файла, слияние, выборка из threads."""

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.instagram import RESERVED_PATHS, USERNAME_RE

SOURCES = ("file", "threads", "cli")
CONFIDENCE_RANK = {None: 0, "guessed": 1, "confirmed": 2}
LINK_RE = re.compile(r"(?:instagram\.com|instagr\.am)/([^/?#\s]+)", re.IGNORECASE)

R_EMPTY = "пустая строка"
R_RESERVED = "служебный путь (пост, рилс, explore…)"
R_INVALID = "не похоже на ник"
R_DUP = "повтор в пакете"


@dataclass(frozen=True)
class Candidate:
    """Профиль к занесению; username всегда в нижнем регистре."""

    username: str
    source: str
    source_ref: str | None = None
    sources: tuple[str, ...] = ()
    threads_profile_id: int | None = None
    threads_username: str | None = None
    link_confidence: str | None = None
    link_source: str | None = None

    def __post_init__(self) -> None:
        if not self.sources:
            object.__setattr__(self, "sources", (self.source,))


@dataclass
class Rejected:
    """Отброшенные записи: причина -> примеры (не больше пяти на причину)."""

    counts: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def add(self, reason: str, raw: str) -> None:
        self.counts[reason] += 1
        ex = self.examples.setdefault(reason, [])
        if len(ex) < 5:
            ex.append(raw.strip()[:60])

    def print(self) -> None:
        """Причины отказа с примерами (ничего не печатает, если отбрасывать нечего)."""
        total = sum(self.counts.values())
        if not total:
            return
        print(f"  отброшено         : {total}")
        for reason, n in self.counts.most_common():
            print(f"    {reason}: {n}  (напр. {', '.join(self.examples[reason])})")


def normalize_nick(raw: str) -> tuple[str | None, str | None]:
    """Ник, `@ник` или ссылка -> (ник в нижнем регистре, None) либо (None, причина отказа)."""
    s = raw.strip()
    if not s:
        return None, R_EMPTY
    m = LINK_RE.search(s)
    if m:
        s = m.group(1)
    elif "/" in s or s.lower().startswith(("http:", "https:")):
        # ссылка не на instagram.com (или обрезанная): ник из неё не достать
        return None, R_INVALID
    s = s.strip().lstrip("@").rstrip("/")
    if s.lower() in RESERVED_PATHS:
        return None, R_RESERVED
    if not USERNAME_RE.match(s):
        return None, R_INVALID
    return s.lower(), None


def parse_lines(lines: list[str]) -> list[str]:
    """Строки файла без пустых и комментариев (`#` до конца строки)."""
    out = []
    for line in lines:
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def read_nick_file(path: Path) -> list[str]:
    """Читает файл со списком (utf-8, BOM допускается); нет файла — понятная ошибка."""
    if not path.is_file():
        raise SystemExit(f"Файл со списком профилей не найден: {path}")
    return parse_lines(path.read_text(encoding="utf-8-sig").splitlines())


def to_candidates(
    raws: list[str], source: str, source_ref: str | None, rejected: Rejected
) -> list[Candidate]:
    """Нормализует строки; отброшенные попадают в rejected, повторы внутри списка схлопываются."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for raw in raws:
        nick, reason = normalize_nick(raw)
        if nick is None:
            rejected.add(reason or R_INVALID, raw)
        elif nick in seen:
            rejected.add(R_DUP, raw)
        else:
            seen.add(nick)
            out.append(Candidate(nick, source, source_ref))
    return out


def merge_candidates(a: Candidate, b: Candidate) -> Candidate:
    """Один ник из двух источников в пакете: первый остаётся основным, связь берётся по рангу."""
    sources = a.sources + tuple(s for s in b.sources if s not in a.sources)
    out = replace(a, sources=sources)
    if CONFIDENCE_RANK[b.link_confidence] > CONFIDENCE_RANK[a.link_confidence]:
        out = replace(
            out,
            link_confidence=b.link_confidence,
            link_source=b.link_source,
            threads_profile_id=b.threads_profile_id,
            threads_username=b.threads_username,
        )
    return out


def collapse(cands: list[Candidate]) -> list[Candidate]:
    """Сводит кандидатов с одним ником в одного (порядок первого появления)."""
    by_nick: dict[str, Candidate] = {}
    for c in cands:
        first = by_nick.get(c.username)
        by_nick[c.username] = c if first is None else merge_candidates(first, c)
    return list(by_nick.values())


@dataclass(frozen=True)
class Update:
    """Новые значения колонок существующего профиля и что именно изменилось."""

    values: dict
    sources_added: bool
    link_raised: bool


def plan_update(existing: dict, cand: Candidate) -> Update | None:
    """Что изменить в уже существующем профиле (None — ничего).

    `source` не меняется; новый источник добавляется в `sources`; связь с threads и
    `link_confidence` обновляются только вверх (нет < guessed < confirmed)."""
    old_sources = list(existing.get("sources") or [])
    sources = old_sources + [s for s in cand.sources if s not in old_sources]
    values = {
        "sources": sources,
        "link_confidence": existing.get("link_confidence"),
        "link_source": existing.get("link_source"),
        "threads_profile_id": existing.get("threads_profile_id"),
        "threads_username": existing.get("threads_username"),
    }
    old_rank = CONFIDENCE_RANK[existing.get("link_confidence")]
    raised = CONFIDENCE_RANK[cand.link_confidence] > old_rank
    if raised:
        values.update(
            link_confidence=cand.link_confidence,
            link_source=cand.link_source,
            threads_profile_id=cand.threads_profile_id,
            threads_username=cand.threads_username,
        )
    added = len(sources) != len(old_sources)
    return Update(values, added, raised) if (added or raised) else None


def threads_query(
    confidence: str = "confirmed",
    target_only: bool = False,
    location: str | None = None,
    min_followers: int | None = None,
    category: int | None = None,
    limit: int = 0,
) -> tuple[str, list]:
    """SQL и параметры выборки связей из threads (читаем только threads.*, пишем в instagram)."""
    where, args = [], []

    def add(cond: str, value) -> None:
        args.append(value)
        where.append(cond.replace("?", f"${len(args)}"))

    if confidence != "all":
        add("l.confidence = ?", confidence)
    if target_only:
        where.append("p.is_target")
    if location:
        add("p.about_location ILIKE '%' || ? || '%'", location)
    if min_followers is not None:
        add("p.followers_count >= ?", min_followers)
    if category is not None:
        add("p.category_id = ?", category)
    sql = (
        "SELECT l.id AS link_id, l.instagram_username, l.confidence, l.source AS link_source,\n"
        "       p.id AS threads_profile_id, p.username AS threads_username\n"
        "FROM threads.instagram_links l JOIN threads.profiles p ON p.id = l.profile_id\n"
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + "\n"
    sql += "ORDER BY p.followers_count DESC NULLS LAST, l.id"
    if limit > 0:
        args.append(limit)
        sql += f"\nLIMIT ${len(args)}"
    return sql, args


def threads_candidates(rows: list[dict], rejected: Rejected) -> list[Candidate]:
    """Строки threads.instagram_links -> кандидаты (ник нормализуется так же, как из файла)."""
    out = []
    for r in rows:
        nick, reason = normalize_nick(r["instagram_username"])
        if nick is None:
            rejected.add(reason or R_INVALID, r["instagram_username"])
            continue
        out.append(
            Candidate(
                nick,
                "threads",
                f"threads.instagram_links#{r['link_id']}",
                threads_profile_id=r["threads_profile_id"],
                threads_username=r["threads_username"],
                link_confidence=r["confidence"],
                link_source=r["link_source"],
            )
        )
    return out
