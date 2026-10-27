"""Подгрузка ответов одного узла дерева комментариев."""

import time

from playwright.async_api import Error as PlaywrightError

from browser.net import Net
from core.logs import log
from scrape.consts import (
    LOAD_TIMEOUT_SEC,
    REPLIES_API,
    REPLIES_WAIT_MS,
    SCROLL_MAX_MS,
    SCROLL_QUIET_MS,
    STALL_LIMIT,
    STALL_LIMIT_API,
    STALL_LIMIT_UNKNOWN,
)
from scrape.harvest import Harvester, pick_children
from scrape.page import click_more, page_infos, scroll_down, settle


async def load_children(
    page, net: Net, h: Harvester, node: dict, known_ts: int | None = None
) -> list[dict]:
    """Собирает прямые ответы узла.

    expected == 0: ответов нет, ждать нечего. expected is None: счётчика в JSON
    нет, поэтому прокручиваем, пока что-то подгружается. Иначе прокручиваем,
    пока найдено меньше, чем заявлено. Прогрессом считаются и новые ответы, и
    рост общего пула данных (пачка могла прийти, но ещё не относиться к узлу).

    known_ts (инкрементальный режим): время самого свежего уже сохранённого
    ответа этого узла. Страница отсортирована от новых к старым, поэтому идём
    вниз, пока не встретим ответ не новее известного, и останавливаемся.
    Если порядок на странице оказался не «новые сверху», режим отключается для
    узла и ответы грузятся полностью.
    """

    def children() -> list[dict]:
        return pick_children(h.pool, node["code"], node["author"], node["ts"], node["ancestors"])

    inc = known_ts is not None

    def reached() -> bool:
        """Дошли до границы: на странице есть ответ не новее известного."""
        return any(int(d.get("taken_at") or 0) <= known_ts for d in children())

    def ordered() -> bool:
        """Ответы на странице идут от новых к старым (иначе граница ненадёжна)."""
        ts = [int(d.get("taken_at") or 0) for d in children()]
        return all(a >= b for a, b in zip(ts, ts[1:]))

    def boundary() -> bool:
        return reached() and ordered()

    expected = node["expected"]
    unknown = expected is None
    h.feed_new(net.captured)
    found = children()
    if expected == 0:
        return found  # листовой узел, ждать нечего
    if not unknown and len(found) >= expected:
        return found  # всё уже пришло вместе со страницей
    if inc and found and boundary():
        return found  # граница уже на первой странице

    def enough() -> bool:
        h.feed_new(net.captured)
        if inc:
            return boundary()
        return (not unknown) and len(children()) >= expected

    # Первая порция ответов приходит отдельным запросом: ждём по факту
    await settle(page, net.captured, until=enough, min_ms=800, quiet_ms=1000, max_ms=6000)
    h.feed_new(net.captured)
    best = len(children())
    pool_size = len(h.pool)

    limit = STALL_LIMIT_UNKNOWN if unknown else STALL_LIMIT
    stall = 0
    started = time.time()
    server_done = False  # сервер сообщил, что следующей страницы нет
    reason = "достигнуто заявленное число"

    def need_more() -> bool:
        if inc:
            return not boundary()
        return unknown or best < expected

    while need_more() and time.time() - started < LOAD_TIMEOUT_SEC:
        if inc and not ordered():
            inc = False
            log(
                f"    [~] Порядок ответов не «новые сверху» ({node['code'][:8]}): "
                "инкрементальный режим отключён, грузим полностью"
            )
            continue
        if stall >= limit:
            reason = "нет прогресса несколько прокруток подряд"
            break
        clicked = await click_more(page)
        n0, log0, rp0 = len(net.captured), len(net.calls), len(net.replies_pages)
        try:
            m = await scroll_down(page)
        except PlaywrightError:
            break
        # Ждём именно запрос подгрузки ответов (он приходит не сразу), затем
        # короткая тишина. Если запроса нет, работает обычное ожидание.
        waited = 0
        wait_ms = REPLIES_WAIT_MS if m.get("moved") else 1500
        while waited < wait_ms and not any(REPLIES_API in a for a, _, _ in net.calls[log0:]):
            await page.wait_for_timeout(150)
            waited += 150
        got_api = any(REPLIES_API in a for a, _, _ in net.calls[log0:])
        await settle(
            page,
            net.captured,
            until=enough,
            min_ms=0,
            quiet_ms=500 if got_api else SCROLL_QUIET_MS,
            max_ms=SCROLL_MAX_MS,
        )
        h.feed_new(net.captured)
        n, ps = len(children()), len(h.pool)
        if net.debug:
            calls = (
                ", ".join(f"{a}:{b}{'' if ok else '!'}" for a, b, ok in net.calls[log0:]) or "нет"
            )
            log(
                f"      прокрутка: ответов {n}, объектов {ps}, кнопок «ещё» {clicked}, "
                f"y={m.get('y')}/{m.get('h')}, блоков {m.get('containers')}, "
                f"сдвиг={'да' if m.get('moved') else 'нет'}, "
                f"новых JSON {len(net.captured) - n0}, запросы: {calls}, "
                f"page_info: {page_infos(net.captured[n0:]) or 'нет'}"
            )
        pages = net.replies_pages[rp0:]
        more_on_server = bool(pages) and pages[-1] is True
        if pages and pages[-1] is False:
            server_done = True
        progressed = n > best or ps > pool_size
        if not progressed and not got_api and not m.get("moved"):
            reason = "страница не прокручивается и подгрузки нет"
            break
        if progressed:
            best, pool_size, stall = max(n, best), ps, 0
        else:
            stall += 1
            # Сервер обещает следующую страницу: пустой шаг не повод сдаваться
            if more_on_server and stall >= limit and stall < STALL_LIMIT_API:
                limit = stall + 1
        if server_done and not (n > best):
            reason = "сервер сообщил: следующей страницы нет (has_next_page=False)"
            break
    else:
        if time.time() - started >= LOAD_TIMEOUT_SEC:
            reason = "аварийный потолок времени"

    result = children()
    if inc and boundary():
        log(
            f"    [=] Дошли до известного комментария ({node['code'][:8]}), "
            f"просмотрено ответов: {len(result)}"
        )
    if time.time() - started >= LOAD_TIMEOUT_SEC:
        log(f"    [!] Сработал аварийный потолок {LOAD_TIMEOUT_SEC} с на узел")
    if not inc and not unknown and len(result) < expected:
        why: dict = {}
        pick_children(h.pool, node["code"], node["author"], node["ts"], node["ancestors"], why)
        reasons = ", ".join(f"{k}: {v}" for k, v in why.items()) or "нет"
        log(
            f"    [~] Подгружено {len(result)} из {expected} ответов "
            f"({node['code'][:8]}). Остановка: {reason}. "
            f"Всего объектов на странице {len(h.pool)}, отброшено: {reasons}"
        )
    return result
