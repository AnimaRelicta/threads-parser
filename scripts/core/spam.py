"""Детектор спам-профилей по ленте: чистая функция без БД и браузера.

Вход: ветки профиля (дата, текст, лайки, ответы, репосты, цитаты) из JSON ленты, то есть
то, что парсер уже получает без открытия веток. Выход: Verdict с баллами и причинами.

Сигналы (баллы в RULES.points):
- no_engagement   почти все ветки без ответов и лайков (ГЛАВНЫЙ: без него бана нет);
- burst           >= 8 веток за любые 10 минут (пачка за секунды; человек так не пишет);
- high_frequency  медиана интервала между ветками <= 30 мин или >= 48 веток в сутки;
- metronome       интервалы почти одинаковые (бот «каждые 15 минут»);
- templated       много почти одинаковых текстов (после нормализации чисел, ссылок, знаков);
- link_spam       большинство веток содержит ссылки.

Бан = есть no_engagement И сумма баллов >= ban_score. Так новостной канал с частыми
постами, но с лайками и ответами, не банится, а редкий мёртвый профиль без частоты тоже.
Если подписчиков >= min_followers (SPAM_MIN_FOLLOWERS), канал не спам при любых сигналах.
Если данных мало (< min_posts веток) вердикт всегда «не спам»: лучше пропустить, чем забанить.
"""

import re
import statistics
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class SpamRules:
    min_posts: int = 8  # меньше веток в выборке: вердикт не выносим
    max_posts: int = 60  # в разбор берём столько самых свежих веток
    min_followers: int = 1000  # подписчиков не меньше: не спам; 0 = проверка отключена
    # no_engagement
    dead_likes_max: int = 1  # ветка «мёртвая»: лайков <= этого, репостов и цитат 0,
    dead_replies_max: int = 3  # ответов <= этого (бот и его подставные дают 1-2 ответа)
    dead_share: float = 0.9
    # burst
    burst_window_sec: int = 600
    burst_posts: int = 8
    # high_frequency
    fast_median_gap_sec: int = 30 * 60
    max_per_day: int = 48
    # metronome
    metro_median_max_sec: int = 3 * 3600
    metro_tolerance: float = 0.25  # доля от медианы...
    metro_tolerance_min_sec: int = 120  # ...но не меньше этого
    metro_share: float = 0.6
    metro_min_gaps: int = 7
    # templated
    dup_jaccard: float = 0.8
    dup_share: float = 0.5
    dup_prefix_words: int = 4  # одинаковое начало текста из стольких слов тоже считается шаблоном
    # link_spam
    link_share: float = 0.7
    # баллы и порог
    points: dict = field(
        default_factory=lambda: {
            "no_engagement": 40,
            "burst": 40,
            "high_frequency": 30,
            "metronome": 30,
            "templated": 25,
            "link_spam": 15,
        }
    )
    ban_score: int = 70


RULES = SpamRules()


def rules_with(min_followers: int) -> SpamRules:
    """Правила по умолчанию с порогом подписчиков из настроек (SPAM_MIN_FOLLOWERS)."""
    return replace(RULES, min_followers=max(0, min_followers))


_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_NUM = re.compile(r"\d+")
_PUNCT = re.compile(r"[^\w\s]", re.U)


@dataclass
class Verdict:
    is_spam: bool
    score: int
    sample: int  # сколько веток разобрано
    signals: dict[str, Any] = field(default_factory=dict)  # сработавшие сигналы и их метрики
    metrics: dict[str, Any] = field(default_factory=dict)  # все посчитанные числа (для отладки)
    reason: str = ""  # человекочитаемо, пишется в profiles.ban_reason


def _num(v) -> int:
    return v if isinstance(v, int) else 0


def _is_dead(p: dict, rules: SpamRules) -> bool:
    return (
        _num(p.get("replies")) <= rules.dead_replies_max
        and _num(p.get("likes")) <= rules.dead_likes_max
        and _num(p.get("reposts")) == 0
        and _num(p.get("quotes")) == 0
    )


def _has_counters(p: dict) -> bool:
    """Счётчики известны: хотя бы ответы или лайки пришли из ленты (None = не знаем)."""
    return p.get("replies") is not None or p.get("likes") is not None


def _normalize(text: str) -> str:
    t = _URL.sub(" ", text.lower())
    t = _NUM.sub("0", t)
    return " ".join(_PUNCT.sub(" ", t).split())


def _shingles(norm: str) -> frozenset:
    w = norm.split()
    if len(w) <= 3:
        return frozenset([tuple(w)])
    return frozenset(tuple(w[i : i + 3]) for i in range(len(w) - 2))


def _dup_share(posts: list[dict], rules: SpamRules) -> tuple[float, int]:
    """(доля веток-повторов среди непустых, число непустых). Ветка-повтор похожа на более раннюю."""
    seen: list[frozenset] = []
    prefixes: set[tuple] = set()
    dups = total = 0
    for p in sorted(posts, key=lambda x: x["ts"]):
        norm = _normalize(p.get("text") or "")
        if not norm:
            continue
        total += 1
        sh = _shingles(norm)
        words = norm.split()
        prefix = (
            tuple(words[: rules.dup_prefix_words]) if len(words) >= rules.dup_prefix_words else None
        )
        if (prefix and prefix in prefixes) or any(
            len(sh & o) / len(sh | o) >= rules.dup_jaccard for o in seen
        ):
            dups += 1
        seen.append(sh)
        if prefix:
            prefixes.add(prefix)
    return (dups / total if total else 0.0), total


def _max_in_window(ts_sorted: list[int], span: int) -> int:
    """Наибольшее число веток в любом окне длиной span секунд."""
    best, left = 0, 0
    for right, t in enumerate(ts_sorted):
        while t - ts_sorted[left] > span:
            left += 1
        best = max(best, right - left + 1)
    return best


def analyze(items: list[dict], rules: SpamRules = RULES, followers: int | None = None) -> Verdict:
    """items: [{'ts', 'text', 'likes', 'replies', 'reposts', 'quotes'}]; ts в секундах (unix).

    followers: число подписчиков профиля, None = неизвестно (тогда порог не применяется)."""
    posts = [p for p in items if p.get("ts") is not None]
    posts.sort(key=lambda p: int(p["ts"]), reverse=True)
    posts = posts[: rules.max_posts]
    n = len(posts)
    if n < rules.min_posts:
        return Verdict(False, 0, n, reason=f"мало веток для вывода ({n} < {rules.min_posts})")

    ts = sorted(int(p["ts"]) for p in posts)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    med_gap = statistics.median(gaps)
    metrics: dict[str, Any] = {"posts": n, "median_gap_sec": med_gap}
    signals: dict[str, Any] = {}

    # --- no_engagement: считаем только ветки с известными счётчиками
    known = [p for p in posts if _has_counters(p)]
    if len(known) >= rules.min_posts:
        share = sum(_is_dead(p, rules) for p in known) / len(known)
        metrics["dead_share"] = round(share, 3)
        if share >= rules.dead_share:
            signals["no_engagement"] = {"dead_share": round(share, 3), "of": len(known)}

    # --- burst
    in_burst = _max_in_window(ts, rules.burst_window_sec)
    metrics["max_in_burst_window"] = in_burst
    if in_burst >= rules.burst_posts:
        signals["burst"] = {"posts": in_burst, "window_min": rules.burst_window_sec // 60}

    # --- high_frequency
    per_day = _max_in_window(ts, 86400)
    metrics["max_per_day"] = per_day
    if med_gap <= rules.fast_median_gap_sec or per_day >= rules.max_per_day:
        signals["high_frequency"] = {
            "median_gap_min": round(med_gap / 60, 1),
            "max_per_day": per_day,
        }

    # --- metronome
    if len(gaps) >= rules.metro_min_gaps and 0 < med_gap <= rules.metro_median_max_sec:
        tol = max(rules.metro_tolerance_min_sec, rules.metro_tolerance * med_gap)
        share = sum(abs(g - med_gap) <= tol for g in gaps) / len(gaps)
        metrics["metronome_share"] = round(share, 3)
        if share >= rules.metro_share:
            signals["metronome"] = {
                "interval_min": round(med_gap / 60, 1),
                "share": round(share, 3),
            }

    # --- templated
    dshare, dtotal = _dup_share(posts, rules)
    metrics["dup_share"] = round(dshare, 3)
    if dtotal >= rules.min_posts and dshare >= rules.dup_share:
        signals["templated"] = {"dup_share": round(dshare, 3), "of": dtotal}

    # --- link_spam
    with_text = [p for p in posts if (p.get("text") or "").strip()]
    if len(with_text) >= rules.min_posts:
        lshare = sum(bool(_URL.search(p["text"])) for p in with_text) / len(with_text)
        metrics["link_share"] = round(lshare, 3)
        if lshare >= rules.link_share:
            signals["link_spam"] = {"share": round(lshare, 3)}

    score = sum(rules.points[k] for k in signals)
    if followers is not None:
        metrics["followers"] = followers
        if rules.min_followers and followers >= rules.min_followers:
            metrics["trusted_by_followers"] = True
            note = f"подписчиков {followers} >= {rules.min_followers}: не спам"
            return Verdict(False, score, n, signals, metrics, note)
    is_spam = "no_engagement" in signals and score >= rules.ban_score
    reason = ""
    if is_spam:
        reason = "спам: " + "; ".join(_describe(k, v) for k, v in signals.items())
    return Verdict(is_spam, score, n, signals, metrics, reason)


def _describe(name: str, d: dict) -> str:
    if name == "burst":
        return f"{d['posts']} веток за {d['window_min']} мин"
    if name == "no_engagement":
        return f"{d['dead_share']:.0%} веток без лайков и почти без ответов"
    if name == "high_frequency":
        return f"частота: медиана {d['median_gap_min']} мин, до {d['max_per_day']} веток в сутки"
    if name == "metronome":
        return f"ровный интервал ~{d['interval_min']} мин ({d['share']:.0%} пауз)"
    if name == "templated":
        return f"{d['dup_share']:.0%} текстов повторяются"
    if name == "link_spam":
        return f"ссылки в {d['share']:.0%} веток"
    return name


def dominant_author(items: list[dict]) -> list[dict]:
    """Оставляет ветки самого частого author_id: JSON ленты содержит и чужие посты
    (родительские, рекомендации). Если author_id нигде нет, возвращает всё как есть."""
    ids = [p.get("author_id") for p in items if p.get("author_id")]
    if not ids:
        return items
    top = max(set(ids), key=ids.count)
    return [p for p in items if p.get("author_id") == top]
