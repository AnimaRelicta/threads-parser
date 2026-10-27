"""Сбор комментариев деревом."""

import asyncio
import random
from collections import deque
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from browser.account import Account
from browser.net import Net
from config import TH
from core.errors import AuthProblem, RateLimited
from core.logs import log, pstat
from core.timeutil import iso, ts_from_iso
from scrape.children import load_children
from scrape.consts import NODE_DELAY
from scrape.extract import caption_of
from scrape.harvest import Harvester
from scrape.media import save_comment_media
from scrape.page import open_node


def make_comment(d: dict, pk: str, parent_id: str, depth: int, files: list, failed: int) -> dict:
    info = d.get("text_post_app_info") or {}
    user = d["user"]["username"]
    rta = info.get("reply_to_author")
    rcount = info.get("direct_reply_count") or 0
    return {
        "id": pk,
        "code": d.get("code"),
        "parent_id": parent_id,  # для уровня 1 это код поста
        "depth": depth,
        "reply_to_author": rta.get("username") if isinstance(rta, dict) else rta,
        "author": user,
        "text": caption_of(d),
        "created_at": iso(d.get("taken_at")),
        "likes": d.get("like_count"),
        "media": files,
        "media_failed": failed,
        "url": f"{TH}/@{user}/post/{d.get('code')}",
        "has_replies": rcount > 0,
        "reply_count": rcount,
        "visited_reply_count": None,  # сколько ответов было, когда зашли внутрь
    }


def root_node_from(media: dict, username: str, shortcode: str) -> dict:
    info = media.get("text_post_app_info") or {}
    return {
        "code": shortcode,
        "id": shortcode,
        "author": username,
        "ts": media.get("taken_at"),
        "ancestors": (),
        "depth": 0,
        "expected": info.get("direct_reply_count"),
    }  # None = счётчика нет


def node_from_comment(c: dict, comments: dict, root_code: str) -> dict:
    """Узел обхода по уже сохранённому комментарию (путь предков из parent_id)."""
    chain: list[str] = []
    pid = c.get("parent_id")
    guard = 0
    while pid and pid != root_code and pid in comments and guard < 50:
        chain.append(comments[pid].get("code") or pid)
        pid = comments[pid].get("parent_id")
        guard += 1
    return {
        "code": c["code"],
        "id": c["id"],
        "author": c["author"],
        "ts": ts_from_iso(c.get("created_at")),
        "ancestors": (root_code, *reversed(chain)),
        "depth": c.get("depth", 1),
        "expected": c.get("reply_count") or 0,
    }


async def collect_thread(
    acc: Account,
    page,
    net: Net,
    root_h: Harvester,
    root: dict,
    existing: list[dict],
    media_dir: Path,
    args,
    deep: bool = False,
    root_visited: bool = False,
) -> tuple[list, dict]:
    """Обход дерева ответов поста по ширине.

    Возвращает (все комментарии, статистика). Уже известные комментарии не
    скачиваются заново; внутрь заходим, только если у узла есть ответы, глубина
    не исчерпана и число ответов выросло с прошлого захода. Если deep=True
    (свежий пост), заходим во все узлы с ответами: рост на глубине виден только
    на странице родителя. Узлы, до которых раньше не дошли из-за глубины, берём
    напрямую из сохранённых данных, не открывая их родителей.
    """
    comments: dict[str, dict] = {c["id"]: c for c in existing}
    cap = args.max_comments or 0
    stats = {"new": 0, "opened": 0, "complete": True, "error": None}
    scheduled: set[str] = set()
    queue: deque = deque([(root, root_h)])
    errors_in_row = 0
    capped = False

    # Узлы, внутрь которых ещё не заходили (например, раньше глубина была меньше)
    for c in sorted(existing, key=lambda x: x.get("depth", 1)):
        if (
            c.get("code")
            and c.get("author")
            and c["id"] not in scheduled
            and c.get("depth", 1) < args.depth
            and (c.get("reply_count") or 0) > 0
            and c.get("visited_reply_count") is None
        ):
            scheduled.add(c["id"])
            queue.append((node_from_comment(c, comments, root["code"]), None))

    while queue and not capped:
        node, h = queue.popleft()

        # Глубина могла уточниться, пока узел стоял в очереди
        if node["id"] in comments:
            node["depth"] = comments[node["id"]].get("depth", node["depth"])
        if node["depth"] >= args.depth:
            continue

        try:
            if h is None:
                await asyncio.sleep(random.uniform(*NODE_DELAY))
                h = await open_node(page, net, node["author"], node["code"])
                stats["opened"] += 1
                pstat(pages_opened=1)
                fresh = h.pool.get(node["code"])
                if fresh is not None:
                    cnt = (fresh.get("text_post_app_info") or {}).get("direct_reply_count")
                    if cnt is not None:
                        node["expected"] = cnt
            known_ts = None
            if not getattr(args, "full_rescan", False):
                visited_ok = (
                    root_visited
                    if node["depth"] == 0
                    else comments.get(node["id"], {}).get("visited_reply_count") is not None
                )
                if visited_ok:
                    tss = [
                        ts_from_iso(c.get("created_at"))
                        for c in comments.values()
                        if c.get("parent_id") == node["id"]
                    ]
                    tss = [t for t in tss if t is not None]
                    known_ts = max(tss) if tss else None
            children = await load_children(page, net, h, node, known_ts)
        except (RateLimited, AuthProblem) as e:
            stats["error"], stats["complete"] = e, False
            break
        except PlaywrightError as e:
            log(f"    [!] Ошибка на узле {node['code']}: {str(e)[:120]}")
            stats["complete"] = False
            errors_in_row += 1
            if errors_in_row >= 3:
                break
            continue
        errors_in_row = 0

        parent_id = node["id"]
        child_depth = node["depth"] + 1
        new_here = 0
        for d in sorted(children, key=lambda x: x.get("taken_at") or 0):
            pk = str(d.get("pk") or d["code"])
            info = d.get("text_post_app_info") or {}
            rcount = info.get("direct_reply_count") or 0
            known = comments.get(pk)

            if known is None:
                if cap and len(comments) >= cap:
                    capped = True
                    break
                files, failed = await save_comment_media(acc, d, pk, media_dir, args)
                c = make_comment(d, pk, parent_id, child_depth, files, failed)
                comments[pk] = c
                stats["new"] += 1
                new_here += 1
            else:
                c = known
                c["likes"] = d.get("like_count", c.get("likes"))
                c["reply_count"] = rcount
                c["has_replies"] = rcount > 0
                # Более глубокая привязка точнее: её даёт страница настоящего
                # родителя, а на странице деда вложенный ответ виден «внутри»
                if child_depth >= c.get("depth", 0):
                    c["parent_id"], c["depth"] = parent_id, child_depth
                if c.get("media_failed", 0) > 0:
                    c["media"], c["media_failed"] = await save_comment_media(
                        acc, d, pk, media_dir, args
                    )

            # Планируем заход внутрь: только если есть что искать
            if child_depth < args.depth and rcount > 0 and pk not in scheduled:
                visited = c.get("visited_reply_count")
                if deep or visited is None or rcount > visited:
                    scheduled.add(pk)
                    queue.append(
                        (
                            {
                                "code": d["code"],
                                "id": pk,
                                "author": d["user"]["username"],
                                "ts": d.get("taken_at"),
                                "ancestors": node["ancestors"] + (node["code"],),
                                "depth": child_depth,
                                "expected": rcount,
                            },
                            None,
                        )
                    )

        if node["id"] in comments:
            comments[node["id"]]["visited_reply_count"] = (
                node["expected"] if node["expected"] is not None else len(children)
            )
        label = "пост" if node["depth"] == 0 else f"ур.{node['depth']} {node['code'][:8]}"
        log(
            f"    [{label}] ответов {len(children)}/{'?' if node['expected'] is None else node['expected']}, новых {new_here}"
        )

    if capped:
        log(f"    [!] Достигнут лимит комментариев на пост ({cap})")
    return list(comments.values()), stats


def finalize_comments(post: dict, root: dict, comments: list, stats: dict, args) -> None:
    comments.sort(key=lambda c: c.get("created_at") or "")
    post["comments"] = comments
    post["_stats_new"] = stats["new"]  # временное поле, run_profile его забирает
    if stats["complete"]:
        post["visited_reply_count"] = (
            root["expected"]
            if root["expected"] is not None
            else sum(1 for c in comments if c.get("depth") == 1)
        )
        post["comments_depth"] = max(post.get("comments_depth", 0), args.depth)
    top = sum(1 for c in comments if c.get("depth") == 1)
    log(
        f"    [+] Комментариев всего: {len(comments)} (уровень 1: {top}, "
        f"глубже: {len(comments) - top}), новых: {stats['new']}, "
        f"открыто страниц: {stats['opened']}"
    )
