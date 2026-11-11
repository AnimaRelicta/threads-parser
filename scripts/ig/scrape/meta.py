"""--profile-only: метаданные профиля Instagram (API веб-клиента; запасные пути: JSON, og:*)."""

import json

from core.logs import log
from ig.consts import JS_FETCH, PROFILE_INFO_URL, USER_INFO_URL
from ig.db.store import IgStore
from ig.extract import (
    classify_api,
    find_embedded_user,
    meta_from_og,
    private_from_text,
    user_from_dict,
)
from ig.scrape.entry import fail_auth, fail_rate, open_profile
from ig.scrape.report import new_result

PAGE_JS = """() => ({
    og_title: document.querySelector('meta[property="og:title"]')?.content || null,
    og_description:
        document.querySelector('meta[property="og:description"]')?.content || null,
    text: (document.body?.innerText || '').slice(0, 4000)
})"""
JSON_SCRIPTS_JS = """() => [...document.querySelectorAll('script[type="application/json"]')]
    .map(e => e.textContent).filter(t => t && (t.includes('biography') || t.includes('follower')))"""


async def fetch_api(page, url: str) -> tuple[str, dict | None]:
    """Запрос API из контекста страницы -> (вид, объект пользователя), см. classify_api."""
    return classify_api(await page.evaluate(JS_FETCH, url))


async def renamed_to(page, store: IgStore, username: str) -> str | None:
    """Новый ник, если профиль переименован: по сохранённому ig_user_id (users/{id}/info)."""
    uid = await store.stored_user_id(username)
    if not uid:
        return None
    kind, user = await fetch_api(page, USER_INFO_URL.format(uid=uid))
    new = (user or {}).get("username")
    if kind == "user" and new and new.lower() != username.lower():
        return new
    return None


async def meta_from_page(page, username: str) -> tuple[dict | None, dict | None, str | None]:
    """Запасной путь без API: JSON страницы, затем og:*. -> (поля, сырое, источник)."""
    docs = []
    try:
        for text in await page.evaluate(JSON_SCRIPTS_JS):
            try:
                docs.append(json.loads(text))
            except ValueError:
                pass
    except Exception:
        pass
    raw_user = find_embedded_user(docs, username)
    info = await page.evaluate(PAGE_JS)
    if raw_user:
        meta = user_from_dict(raw_user)
        if meta.get("is_private") is None and private_from_text(info["text"]):
            meta["is_private"] = True
        return meta, raw_user, "embedded"
    meta = meta_from_og(info["og_title"], info["og_description"])
    if private_from_text(info["text"]):
        meta["is_private"] = True
    if not meta:
        return None, None, None
    return meta, {"og_title": info["og_title"], "og_description": info["og_description"]}, "og"


async def read_api(
    page, username: str, account: str
) -> tuple[dict | None, str | None, dict | None]:
    """Открывает профиль и просит API -> (отказ для result, вид ответа, объект пользователя)."""
    fail = await open_profile(page, username, account)
    if fail:
        return fail, None, None
    kind, user = await fetch_api(page, PROFILE_INFO_URL.format(username=username))
    if kind == "rate":
        return fail_rate(), kind, None
    if kind == "auth":
        note = f"API отказал (401/403): сессия недействительна? ig accounts relogin {account}"
        return fail_auth(note), kind, None
    if kind == "not_found":
        return {"status": "not_found", "note": "профиль не найден (API)"}, kind, None
    return None, kind, user


async def run_profile_only(page, username: str, args, store: IgStore, account: str) -> dict:
    """Метаданные одного профиля -> profiles; возвращает результат для summary и profile_runs."""
    res = new_result(username, "profile", stop_reason="end")
    fail, kind, user = await read_api(page, username, account)
    if fail and fail["status"] == "not_found":
        new = await renamed_to(page, store, username)  # ник сменили: ищем по ig_user_id
        if new and await store.rename(username, new):
            res["note"] = f"ник изменён: @{username} → @{new}"
            log(f"[+] {res['note']}")
            username = res["username"] = new
            fail, kind, user = await read_api(page, username, account)
    if fail:
        res.update(fail, note=join_notes(res["note"], fail["note"]))
        if fail["status"] == "not_found":
            await store.save_meta(username, "not_found", None, None)
        return res
    return await _save_result(page, username, args, store, res, kind, user)


def join_notes(*notes: str | None) -> str:
    return "; ".join(n for n in notes if n)


async def _save_result(page, username, args, store, res, kind, user) -> dict:
    """Собирает поля из ответа API (или запасного пути) и пишет их в БД."""
    if kind == "user" and user:
        meta, raw, src = user_from_dict(user), user, "api"
    else:
        meta, raw, src = await meta_from_page(page, username)
        if meta is None:
            res.update(status="error", note="данные профиля не получены (проверить на живой)")
            await store.mark_error(username)
            return res
        res["note"] = join_notes(res["note"], f"запасной путь: {src}")
    known = meta.get("ig_user_id")
    stored = await store.stored_user_id(username)
    if stored and known and stored != known:
        res["note"] = join_notes(res["note"], "ник теперь у другого владельца")
    status = "private" if meta.get("is_private") else "ok"
    dup = await store.save_meta(username, status, meta, {"source": src, "raw": raw})
    if dup:
        res["note"] = join_notes(res["note"], dup)
    res.update(status=status, followers=meta.get("followers_count"))
    if getattr(args, "debug", False):
        log(f"[debug] {src}: {sorted(k for k, v in meta.items() if v is not None)}")
    log(
        f"[+] @{username} | {meta.get('full_name') or '—'} | подписчиков: "
        f"{meta.get('followers_count') if meta.get('followers_count') is not None else '?'}"
        f" | {status} | источник: {src}"
    )
    return res
