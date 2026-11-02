"""Классы спам-профилей по первым веткам ленты: чистая функция без БД и браузера.

Отдельно от `core/spam.py` (тот ловит ботов и банит): здесь профиль только получает метку
класса, бан не выставляется. Вход: ветки профиля (дата, ответы) из ленты, берутся
`ClassRules.sample` (по умолчанию 10) самых свежих. Метрики:

- частота: медиана пауз между ветками выборки; «часто» = меньше `frequent_gap_sec`;
- комментарии: медиана `replies` по веткам выборки с известным счётчиком.

Классы (приоритет: 3 важнее 2):
1 board_author   веток постит не часто, комментариев много (медиана >= many_replies):
                 автор ведёт «барахолку», люди сами пишут в комментах, что продают;
2 seller         веток постит часто, комментариев мало (медиана <= few_replies):
                 выкладывает свои товары/объявления в ветки;
3 spammer        как 2, и подписчиков меньше low_followers (число должно быть известно).

Меньше `min_threads` веток с датой или с известным счётчиком ответов: класса нет.
"""

import statistics
from dataclasses import dataclass, field, replace
from typing import Any

BOARD_AUTHOR = 1
SELLER = 2
SPAMMER = 3

CLASS_NAMES = {
    BOARD_AUTHOR: "board_author",
    SELLER: "seller",
    SPAMMER: "spammer",
}
CLASS_TITLES = {
    BOARD_AUTHOR: "барахолка: редкие ветки, много комментариев",
    SELLER: "продавец: частые ветки, мало комментариев",
    SPAMMER: "спамер: частые ветки, мало комментариев, мало подписчиков",
}


@dataclass(frozen=True)
class ClassRules:
    sample: int = 10  # сколько самых свежих веток берём в выборку
    min_threads: int = 5  # меньше веток (с датой / с известным счётчиком): класса нет
    frequent_gap_sec: int = 24 * 3600  # медиана пауз меньше этого: постит часто
    many_replies: int = 20  # медиана ответов >= этого: комментариев много
    few_replies: int = 5  # медиана ответов <= этого: комментариев мало
    low_followers: int = 100  # подписчиков меньше этого (и число известно): класс 3


CLASS_RULES = ClassRules()


def class_rules_with(
    sample: int | None = None,
    low_followers: int | None = None,
    frequent_gap_hours: float | None = None,
    many_replies: int | None = None,
    few_replies: int | None = None,
) -> ClassRules:
    """Правила по умолчанию с порогами из настроек (SPAM_CLASS_*); None = оставить как есть."""
    changes: dict[str, Any] = {}
    if sample is not None:
        changes["sample"] = max(1, sample)
        changes["min_threads"] = min(CLASS_RULES.min_threads, max(1, sample))
    if low_followers is not None:
        changes["low_followers"] = max(0, low_followers)
    if frequent_gap_hours is not None:
        changes["frequent_gap_sec"] = max(1, int(frequent_gap_hours * 3600))
    if many_replies is not None:
        changes["many_replies"] = max(0, many_replies)
    if few_replies is not None:
        changes["few_replies"] = max(0, few_replies)
    return replace(CLASS_RULES, **changes)


@dataclass
class ClassVerdict:
    code: int | None  # 1, 2, 3 или None (класса нет)
    sample: int  # веток с датой в выборке
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""  # человекочитаемо; при code=None объясняет, почему класса нет
    no_data: bool = False  # данных мало: вердикта нет, прежний класс в БД трогать нельзя

    @property
    def name(self) -> str | None:
        return CLASS_NAMES.get(self.code) if self.code else None


def classify(
    items: list[dict], followers: int | None = None, rules: ClassRules = CLASS_RULES
) -> ClassVerdict:
    """items: [{'ts', 'replies', ...}] в любом порядке; ts в секундах (unix).

    followers: подписчики профиля, None = неизвестно (тогда класс 3 не назначается)."""
    posts = [p for p in items if p.get("ts") is not None]
    posts.sort(key=lambda p: int(p["ts"]), reverse=True)  # закреплённая старая ветка не мешает
    posts = posts[: rules.sample]
    n = len(posts)
    if n < rules.min_threads:
        return ClassVerdict(
            None, n, reason=f"мало веток для класса ({n} < {rules.min_threads})", no_data=True
        )

    ts = sorted(int(p["ts"]) for p in posts)
    med_gap = statistics.median(b - a for a, b in zip(ts, ts[1:]))
    replies = [p["replies"] for p in posts if isinstance(p.get("replies"), int)]
    metrics: dict[str, Any] = {
        "threads": n,
        "median_gap_hours": round(med_gap / 3600, 2),
    }
    if len(replies) < rules.min_threads:
        return ClassVerdict(
            None, n, metrics, f"счётчик ответов известен у {len(replies)} веток из {n}", True
        )
    med_replies = statistics.median(replies)
    metrics["median_replies"] = med_replies
    metrics["replies_known"] = len(replies)
    if followers is not None:
        metrics["followers"] = followers

    frequent = med_gap < rules.frequent_gap_sec
    gap_txt = f"пауза между ветками {metrics['median_gap_hours']} ч (медиана)"
    rep_txt = f"ответов {med_replies:g} (медиана) на {n} веток"

    if not frequent and med_replies >= rules.many_replies:
        return ClassVerdict(BOARD_AUTHOR, n, metrics, f"{gap_txt}, {rep_txt}: барахолка")
    if frequent and med_replies <= rules.few_replies:
        if followers is not None and followers < rules.low_followers:
            return ClassVerdict(
                SPAMMER,
                n,
                metrics,
                f"{gap_txt}, {rep_txt}, подписчиков {followers} < {rules.low_followers}",
            )
        return ClassVerdict(SELLER, n, metrics, f"{gap_txt}, {rep_txt}: продаёт своё")
    return ClassVerdict(None, n, metrics, f"{gap_txt}, {rep_txt}: ни один класс не подошёл")
