"""Отбор профилей по классу 1/2/3 (`--only-class`): решение до обхода веток.

Три шага, от дешёвого к дорогому:
1. `precheck`: класс уже посчитан раньше (`profiles.spam_class_at` не пуст), берём его из БД,
   браузер не открываем. Профиль чужого класса пропускается сразу.
2. `gate_feed`: класса ещё нет. Читаем из ленты первые `SPAM_CLASS_SAMPLE` (10) веток, без
   открытия и без записи в `posts`, считаем класс и сохраняем его в профиль. Подходит — обход
   идёт дальше, и эти ветки сохраняются обычным путём. Не подходит — ветки не пишутся
   вовсе, в БД остаётся только метка класса: при следующем запуске сработает шаг 1.
3. `gate_db`: то же для `--fill-threads`, но по веткам, уже лежащим в БД (браузера нет).

Мало данных для класса (меньше `min_threads` веток): профиль пропускается, метка не пишется,
при следующем запуске детекция повторится.
"""

import contextlib
from dataclasses import dataclass

from core.logs import log
from core.spamclass import CLASS_NAMES, ClassVerdict
from db.profiles import ProfileStore
from scrape.consts import NO_DATA_WAIT_MS
from scrape.feed import iter_post_links
from scrape.spamcheck import check_class, feed_items


@dataclass
class Gate:
    keep: bool  # True: профиль нужного класса, обход продолжается
    note: str  # объяснение для лога и `profile_runs.note`
    final: bool = True  # False: класс не определён, профиль не считается обработанным


def _decide(code: int | None, wanted: frozenset[int], source: str) -> Gate:
    label = f"{code} ({CLASS_NAMES[code]})" if code else "нет"
    if code in wanted:
        return Gate(True, f"класс {label}, {source}")
    return Gate(False, f"класс {label} не входит в --only-class, {source}")


def skip(result: dict, gate: Gate, username: str) -> dict:
    """Итог пропущенного профиля: статус skipped, причина в note."""
    log(f"[CLASS] @{username}: {gate.note}: пропускаю")
    result.update(
        status="skipped",
        stop_reason="class" if gate.final else "class_undetermined",
        note=gate.note,
    )
    result.setdefault("counts", {})
    return result


async def precheck(store: ProfileStore, profile_id: int, wanted: frozenset[int]) -> Gate | None:
    """Решение по классу из БД; None, если класс ещё не считали."""
    code, at = await store.class_state(profile_id)
    return None if at is None else _decide(code, wanted, "из БД")


def _undetermined(cv: ClassVerdict) -> Gate:
    return Gate(
        False, f"класс не определён ({cv.reason}), повторится при следующем запуске", final=False
    )


async def detect_feed(
    grid_page, net, feed, store: ProfileStore, profile_id: int, username: str
) -> ClassVerdict:
    """Класс по первым веткам ленты профиля; пишет метку в БД, ветки не сохраняет."""
    sample = store.class_rules.sample
    infos: list[dict] = []
    async with contextlib.aclosing(iter_post_links(grid_page, True, username)) as links:
        async for code, _ in links:
            feed.update(net.grid_captured)
            info = feed.items.get(code)
            if info is None:  # JSON ленты может прийти чуть позже ссылки
                await grid_page.wait_for_timeout(NO_DATA_WAIT_MS)
                feed.update(net.grid_captured)
                info = feed.items.get(code)
            if info is None:
                continue
            infos.append(info)
            if len(infos) >= sample:
                break
    await grid_page.evaluate("window.scrollTo(0, 0)")  # основной обход начнёт с начала ленты
    return await check_class(
        store, profile_id, username, feed_items(infos), await store.followers_count(profile_id)
    )


async def gate_feed(
    grid_page,
    net,
    feed,
    store: ProfileStore,
    profile_id: int,
    username: str,
    wanted: frozenset[int],
) -> Gate:
    """Решение после открытия профиля: класс из БД или детекция по первым веткам ленты."""
    gate = await precheck(store, profile_id, wanted)  # check_feed мог посчитать класс при входе
    if gate is None:
        log(f"[*] @{username}: класса ещё нет, читаю первые {store.class_rules.sample} веток")
        cv = await detect_feed(grid_page, net, feed, store, profile_id, username)
        gate = (
            _undetermined(cv) if cv.no_data else _decide(cv.code, wanted, f"по {cv.sample} веткам")
        )
    log(f"[CLASS] @{username}: {gate.note}: {'беру' if gate.keep else 'пропускаю'}")
    return gate


async def gate_db(
    store: ProfileStore, profile_id: int, username: str, wanted: frozenset[int]
) -> Gate:
    """Решение для `--fill-threads`: класс из БД или по веткам, сохранённым в `posts`."""
    gate = await precheck(store, profile_id, wanted)
    if gate is None:
        cv = await store.detect_class_db(profile_id)
        gate = (
            _undetermined(cv) if cv.no_data else _decide(cv.code, wanted, f"по {cv.sample} веткам")
        )
    log(f"[CLASS] @{username}: {gate.note}: {'беру' if gate.keep else 'пропускаю'}")
    return gate
