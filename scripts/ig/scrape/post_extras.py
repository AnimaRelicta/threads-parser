"""Содержимое поста для полного прохода и --fill: комментарии (до 2 уровней) и медиа-файлы."""

import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Error as PlaywrightError

from core.errors import AuthProblem, RateLimited
from core.logs import log
from ig.consts import CHILD_COMMENTS_URL, COMMENTS_URL, JS_FETCH
from ig.db.store import IgStore
from ig.feed import classify_error, comment_from_dict, media_slides
from ig.storage import avatar_rel, download, ensure_space, ext_for, rel_path

PAUSE = (1.0, 2.5)  # пауза между запросами комментариев, с
SPACE_CHECK_EVERY = 25  # файлов между проверками свободного места


class Extras:
    """Комментарии и медиа постов одного профиля; счётчики идут в profile_runs.

    states — что уже собрано по постам профиля (store.post_states): по нему решается, нужен ли
    уже известный пост перепроверке."""

    def __init__(self, page, username: str, args, store: IgStore):
        self.page, self.username, self.args, self.store = page, username, args, store
        self.states: dict[str, dict] = {}
        self.comments = {"new": 0}
        self.media = {
            "new_files": 0,
            "new_images": 0,
            "new_videos": 0,
            "new_bytes": 0,
            "download_failed": 0,
        }

    async def load(self) -> None:
        self.states = await self.store.post_states(self.username)

    def needs(self, post: dict) -> tuple[bool, bool, bool]:
        """(медиа, комментарии, первая полная обработка) для поста."""
        a, state = self.args, self.states.get(post["code"])
        want_media = bool(a.images or a.videos)
        want_comments = bool(a.comments and not post["comments_disabled"])
        if state is None or state["last_refreshed_at"] is None:
            return want_media, want_comments, True
        media = want_media and (
            state["media_status"] in ("pending", "partial", "failed")
            or (a.videos and state["media_status"] == "skipped")
        )
        young = False
        if a.recheck_days and state["taken_at"]:
            age = datetime.now(timezone.utc) - state["taken_at"]
            young = age.days < a.recheck_days
        grew = (post["comments_count"] or 0) > (state["comments_collected"] or 0)
        comments = want_comments and (state["comments_status"] != "done" or young or grew)
        return media, comments, False

    async def handle(self, post: dict, item: dict, was_known: bool = False) -> None:
        """Один пост: медиа и комментарии по флагам, затем last_refreshed_at.

        RateLimited/AuthProblem поднимаются наверх: частичное уже записано."""
        do_media, do_comments, first = self.needs(post)
        if not (do_media or do_comments or first):
            return
        code, status, failed = post["code"], None, 0
        if do_media:
            status, failed = await self.get_media(post, item)
        if do_comments:
            await self.get_comments(post)
        await self.store.finish_post(code, status, failed)
        self.states[code] = {
            **(self.states.get(code) or {}),
            "last_refreshed_at": datetime.now(timezone.utc),
            "media_status": status or (self.states.get(code) or {}).get("media_status", "pending"),
            "comments_status": "done" if do_comments else "none",
            "comments_collected": post["comments_count"] or 0,
            "taken_at": post["taken_at"],
        }

    # ---------- медиа ----------
    async def get_media(self, post: dict, item: dict) -> tuple[str, int]:
        """Скачивает фото/видео слайдов по флагам -> (media_status, число неудач)."""
        a, code = self.args, post["code"]
        slides = media_slides(item)
        if not slides:
            log(f"    [!] {code}: в ответе нет адресов медиа")
            return "failed", 1
        ok = failed = skipped = 0
        rows = []
        for s in slides:
            if not ((s["kind"] == "image" and a.images) or (s["kind"] == "video" and a.videos)):
                skipped += 1
                continue
            ext = ext_for(s["url"], None, s["kind"])
            rel = rel_path(self.username, post["taken_at"], code, s["position"], ext)
            try:
                if (self.media["new_files"] + 1) % SPACE_CHECK_EVERY == 0:
                    ensure_space(a.storage_dir, a.min_free_gb)
                size, fresh = await download(self.page, s["url"], Path(a.storage_dir) / rel)
            except (OSError, PlaywrightError) as e:
                failed += 1
                self.media["download_failed"] += 1
                log(f"    [!] {code}#{s['position']}: не скачано ({str(e)[:100]})")
                continue
            ok += 1
            rows.append({**s, "file_path": rel, "bytes": size})
            if fresh:
                self.media["new_files"] += 1
                self.media["new_bytes"] += size
                self.media["new_images" if s["kind"] == "image" else "new_videos"] += 1
        await self.store.save_media(code, rows)
        if ok + failed == 0:
            return "skipped", 0
        if ok == 0:
            return "failed", failed
        return ("partial" if failed or skipped else "done"), failed

    # ---------- комментарии ----------
    async def fetch(self, url: str) -> dict | None:
        """JSON-ответ или None (ошибка, пост недоступен); 429/сессия — исключения."""
        res = await self.page.evaluate(JS_FETCH, url)
        bad = classify_error(res)
        if bad == "rate":
            raise RateLimited()
        if bad == "auth":
            raise AuthProblem("сессия недействительна при чтении комментариев (401)")
        return None if bad else res

    async def pause(self) -> None:
        await asyncio.sleep(random.uniform(*PAUSE))

    async def get_comments(self, post: dict) -> None:
        """Комментарии поста: верхний уровень по min_id, при depth>=2 ещё ответы.

        Страница целиком из уже известных комментариев = дальше всё собрано (кроме
        --full-rescan). Лимит --max-comments считает и ответы. 429: частичное сохраняется."""
        a, code = self.args, post["code"]
        known = await self.store.known_comments(code)
        total = 0
        capped = broken = False
        cursor = None
        try:
            while True:
                url = COMMENTS_URL.format(media_id=post["media_id"])
                url += f"&min_id={quote(cursor)}" if cursor else ""
                res = await self.fetch(url)
                if res is None:
                    broken = True
                    break
                rows, all_known, parents = [], True, []
                for c in res.get("comments") or []:
                    row = comment_from_dict(c, code, None)
                    if not row:
                        continue
                    if row["id"] not in known:
                        all_known = False
                        known[row["id"]] = 0
                        self.comments["new"] += 1
                    rows.append(row)
                    parents.append(row)
                    total += 1
                    if a.max_comments and total >= a.max_comments:
                        capped = True
                        break
                await self.store.save_comments(rows)
                if a.depth >= 2:
                    for row in parents:
                        if row["reply_count"] > known.get(row["id"], 0):
                            total, capped = await self.get_replies(post, row, known, total)
                            if capped:
                                break
                nxt = res.get("next_min_id")
                if isinstance(nxt, (dict, list)):
                    nxt = json.dumps(nxt, separators=(",", ":"))
                if capped or not nxt or (all_known and not a.full_rescan):
                    break
                cursor = str(nxt)
                await self.pause()
        except (RateLimited, AuthProblem):
            await self.store.set_comments_state(code, "partial", a.depth)
            raise
        await self.store.set_comments_state(code, "partial" if capped or broken else "done", a.depth)

    async def get_replies(
        self, post: dict, parent: dict, known: dict, total: int
    ) -> tuple[int, bool]:
        """Ответы к комментарию -> (общий счёт, достигнут лимит)."""
        a, cursor = self.args, None
        while True:
            url = CHILD_COMMENTS_URL.format(media_id=post["media_id"], comment_id=parent["id"])
            url += f"max_id={quote(cursor)}" if cursor else ""
            await self.pause()
            res = await self.fetch(url)
            if res is None:
                return total, False
            rows = []
            for c in res.get("child_comments") or []:
                row = comment_from_dict(c, parent["post_code"], parent["id"])
                if not row:
                    continue
                if row["id"] not in known:
                    known[row["id"]] = 0
                    known[parent["id"]] = known.get(parent["id"], 0) + 1
                    self.comments["new"] += 1
                rows.append(row)
                total += 1
                if a.max_comments and total >= a.max_comments:
                    await self.store.save_comments(rows)
                    return total, True
            await self.store.save_comments(rows)
            cursor = res.get("next_max_child_cursor")
            if not (res.get("has_more_child_comments") and cursor):
                return total, False


async def save_avatar(page, username: str, args, store: IgStore) -> str | None:
    """Аватар профиля -> <ник>/avatar.jpg; адрес берётся из profiles.profile_pic_url."""
    url = await store.avatar_url(username)
    if not url:
        return None
    rel = avatar_rel(username, ext_for(url, None, "image"))
    try:
        await download(page, url, Path(args.storage_dir) / rel)
    except (OSError, PlaywrightError) as e:
        log(f"    [!] аватар не скачан: {str(e)[:100]}")
        return None
    await store.set_avatar(username, rel)
    return rel
