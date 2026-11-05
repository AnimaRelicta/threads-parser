"""Разведка структуры выдачи (--recon)."""

import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

from browser.account import Account
from browser.net import Net
from core.logs import log
from registry.store import pick_accounts
from scrape.capture import make_handler
from scrape.page import scroll_down, settle
from serp.extract import (
    NOISY_KEYS,
    caption_text,
    doc_is_useful,
    find_paths,
    harvest_post_pool,
    iter_search_posts,
    read_all_embedded,
)
from serp.tasks import SearchTask


async def run_recon(st, args, task: "SearchTask") -> None:
    """Разведка: один запрос, сырые JSON в output/_raw/<ts>/, поиск needle.

    Переиспользует Account/Net/make_handler/scroll_down/settle/walk из
    functions.py и pick_accounts из accounts.py — вход и куки не трогаем,
    только читаем. Сохраняются не все документы подряд (их десятки, и
    большинство — webpack require[]-боилерплейт и повторные route-экспорты
    без полезных данных), а только те, где walk() находит объект поста
    (code+taken_at) или searchResults/tag_info.
    """
    from datetime import datetime as _dt

    accounts = await pick_accounts(st, args.accounts)
    acc_name = accounts[0]
    needle = args.needle or re.split(r"\s+", task.query.strip())[0:2]
    needle = " ".join(needle) if isinstance(needle, list) else needle

    run_dir = Path(args.output) / "_raw" / f"{_dt.now():%Y%m%d_%H%M%S}_{task.mode}"
    run_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        acc = Account(acc_name, st, args)
        page = await acc.open(p)
        try:
            net = Net(debug=True)
            page.on("response", make_handler(net.captured, net))
            url = task.url()
            log(f"[recon] {task.label()} -> {url}")
            resp = await page.goto(url, wait_until="domcontentloaded")
            if resp is not None:
                log(f"[recon] HTTP {resp.status}")
            await page.wait_for_timeout(st.search_page_wait_ms)

            all_docs = await read_all_embedded(page)
            log(f"[recon] встроенных JSON-документов на старте: {len(all_docs)}")

            max_scrolls = args.max_scrolls or 6
            for i in range(max_scrolls):
                n0 = len(net.captured)
                await scroll_down(page)
                await settle(
                    page,
                    net.captured,
                    min_ms=st.search_scroll_min_ms,
                    quiet_ms=st.search_scroll_quiet_ms,
                    max_ms=st.search_scroll_max_ms,
                )
                log(
                    f"[recon] прокрутка {i + 1}/{max_scrolls}: "
                    f"новых сетевых JSON {len(net.captured) - n0} (всего {len(net.captured)})"
                )
            all_docs += net.captured

            if not all_docs:
                log(
                    "[recon] [!] Ни одного JSON не поймано (ни встроенного, ни "
                    "сетевого) — проверьте вручную, не редирект ли на /login"
                )
                return

            docs = [d for d in all_docs if doc_is_useful(d)]
            log(
                f"[recon] документов всего: {len(all_docs)}, полезных (с постом/"
                f"searchResults/tag_info): {len(docs)}, отброшено как мусор: "
                f"{len(all_docs) - len(docs)}"
            )

            for i, d in enumerate(docs):
                (run_dir / f"doc_{i:03d}.json").write_text(
                    json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            log(f"[recon] сохранено сырых JSON (отфильтрованных): {len(docs)} -> {run_dir}")

            pool = harvest_post_pool(docs)
            log(f"[recon] объектов поста (code+taken_at) по всем документам: {len(pool)}")
            if pool:
                sample_code, sample = next(iter(pool.items()))
                keys = sorted(sample.keys())
                log(f"[recon] ключи верхнего уровня первого поста ({sample_code}): {keys}")
                for k in keys:
                    if k in NOISY_KEYS:
                        log(f"    {k}: <опущено, медиа-структура>")
                        continue
                    v = sample[k]
                    shown = (
                        v
                        if not isinstance(v, (dict, list))
                        else (
                            f"{type(v).__name__} с {len(v)} ключами/элементами"
                            if isinstance(v, dict)
                            else f"список из {len(v)}"
                        )
                    )
                    log(f"    {k} = {shown!r}")
                tpai = sample.get("text_post_app_info")
                if isinstance(tpai, dict):
                    log(f"[recon] ключи text_post_app_info: {sorted(tpai.keys())}")
                log(
                    f"[recon] caption/tag_header по всем {len(pool)} постам пула "
                    "(кандидат на поле темы — text_post_app_info.tag_header):"
                )
                for code, d in pool.items():
                    cap = caption_text(d)
                    tpai = d.get("text_post_app_info") or {}
                    th = tpai.get("tag_header")
                    log(f"    {code}: caption={cap!r} | tag_header={th!r}")
                extracted = iter_search_posts(docs, task.query, task.mode)
                with_topic = sum(1 for p in extracted if p["topic"])
                log(
                    f"[recon] extract_post: {len(extracted)} постов разобрано, "
                    f"тема из JSON найдена у {with_topic}, "
                    f"без темы {len(extracted) - with_topic}"
                )
                for p in extracted:
                    log(
                        f"    {p['code']}: @{p['username']} | тема={p['topic']!r} | "
                        f"текст={(p['text'] or '')[:60]!r}"
                    )
            else:
                log(
                    "[recon] [!] Ни одного объекта поста не найдено среди полезных "
                    "документов — структура могла измениться, нужен --debug-разбор вручную"
                )

            hits: list[tuple[int, str, str]] = []
            for i, d in enumerate(docs):
                for path, val in find_paths(d, needle):
                    hits.append((i, path, val))
            if hits:
                log(f"[recon] найдено «{needle}» в {len(hits)} местах (после фильтра):")
                for i, path, val in hits[:30]:
                    log(f"    doc_{i:03d}.json {path} = {val!r}")
                if len(hits) > 30:
                    log(f"    ... и ещё {len(hits) - 30}")
            else:
                log(
                    f"[recon] [!] «{needle}» не найдено ни в одном документе "
                    f"(документов: {len(docs)}). Проверьте needle (--needle) "
                    "или увеличьте --max-scrolls"
                )
        finally:
            await acc.close()
