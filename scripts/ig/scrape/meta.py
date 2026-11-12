"""--profile-only: метаданные профиля Instagram (API веб-клиента; запасные пути: JSON, og:*)."""

import json

from config import IG
from core.logs import log
from ig.consts import JS_FETCH, PROFILE_INFO_URL, USER_INFO_URL
from ig.db.store import IgStore
from ig.extract import (
    classify_api,
    find_embedded_user,
    header_text,
    meta_from_og,
    meta_from_text,
    not_found_from_text,
    private_from_text,
    user_from_dict,
)
from ig.scrape.about import fetch_about
from ig.scrape.entry import fail_auth, fail_rate, open_profile
from ig.scrape.report import new_result

PAGE_JS = """() => ({
    title: document.title,
    og_title: document.querySelector('meta[property="og:title"]')?.content || null,
    og_description:
        document.querySelector('meta[property="og:description"]')?.content || null,
    text: (document.body?.innerText || '').slice(0, 4000)
})"""
JSON_SCRIPTS_JS = """() => [...document.querySelectorAll('script[type="application/json"]')]
    .map(e => e.textContent)
    .filter(t => t && (t.includes('biography') || t.includes('follower')))"""


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


async def read_page(page, username: str) -> tuple[str, dict | None, dict | None, str | None]:
    """Данные со страницы профиля (API не помог) -> (вид, поля, сырое, источник).

    Вид: ok | gone (профиль недоступен) | none (ничего не нашли). Источники по порядку:
    JSON страницы, видимая шапка (text), og:*."""
    docs = []
    try:
        for text in await page.evaluate(JSON_SCRIPTS_JS):
            try:
                docs.append(json.loads(text))
            except ValueError:
                pass
    except Exception:
        pass
    info = await page.evaluate(PAGE_JS)
    if not_found_from_text(info.get("title"), info.get("text")):
        # причина недоступности — текст самой страницы (в meta_raw, не отдельной колонкой)
        text = header_text(info.get("text"), 500) or (info.get("title") or "").strip()
        reason = {"source": "page", "raw": {"text": text}}
        return "gone", None, reason, None
    raw_user = find_embedded_user(docs, username)
    if raw_user:
        meta, raw, src = user_from_dict(raw_user), raw_user, "embedded"
    else:
        shown = meta_from_text(info.get("text"), username)
        og = meta_from_og(info.get("og_title"), info.get("og_description"))
        meta, src = {**og, **shown}, "text" if shown else "og"
        raw = {"text": header_text(info.get("text"))}
    if meta.get("is_private") is None and private_from_text(info.get("text")):
        meta["is_private"] = True
    return ("ok", meta, raw, src) if meta else ("none", None, None, None)


async def read_api(
    page, username: str, account: str
) -> tuple[dict | None, str | None, dict | None]:
    """Открывает профиль и просит API -> (отказ для result, вид ответа, объект пользователя).

    Отказ только при проблеме страницы, 429 или входе. Любой другой ответ API (в том числе 404)
    не приговор: данные тогда берутся со страницы."""
    fail = await open_profile(page, username, account)
    if fail:
        return fail, None, None
    kind, user = await fetch_api(page, PROFILE_INFO_URL.format(username=username))
    if kind == "rate":
        return fail_rate(), kind, None
    if kind == "auth":
        note = f"API отказал (401/403): сессия недействительна? ig accounts relogin {account}"
        return fail_auth(note), kind, None
    return None, kind, user


async def fetch_profile(page, username: str, account: str):
    """-> (отказ, источник, поля, сырое): API, иначе страница."""
    fail, kind, user = await read_api(page, username, account)
    if fail:
        return fail, None, None, None
    if kind == "user" and user:
        return None, "api", user_from_dict(user), user
    state, meta, raw, src = await read_page(page, username)
    if state == "gone":  # raw здесь — причина {"source", "raw": {"text"}}
        return {"status": "not_found", "note": "профиль не найден", "reason": raw}, None, None, None
    if state == "none":
        note = "данные профиля не получены (проверить на живой сессии)"
        return {"status": "error", "note": note}, None, None, None
    return None, src, meta, raw


async def run_profile_only(page, username: str, args, store: IgStore, account: str) -> dict:
    """Метаданные одного профиля -> profiles; возвращает результат для summary и profile_runs."""
    res = new_result(username, "profile", stop_reason="end")
    fail, src, meta, raw = await fetch_profile(page, username, account)
    if fail and fail["status"] == "not_found":
        new = await renamed_to(page, store, username)  # ник сменили: ищем по ig_user_id
        if new and await store.rename(username, new):
            res["note"] = f"ник изменён: @{username} → @{new}"
            log(f"[+] {res['note']}")
            username = res["username"] = new
            fail, src, meta, raw = await fetch_profile(page, username, account)
    if fail:
        res.update(fail, note=join_notes(res["note"], fail["note"]))
        if fail["status"] == "not_found":
            await store.save_meta(username, "not_found", None, fail.get("reason"))
        elif fail["status"] == "error":
            await store.mark_error(username)
        return res
    res = await _save_result(username, args, store, res, src, meta, raw)
    if res["status"] in ("ok", "private") and not getattr(args, "no_about", False):
        await collect_about(page, username, args, store, res)
    return res


async def collect_about(page, username: str, args, store: IgStore, res: dict) -> None:
    """Окно «Об аккаунте» (··· → Об аккаунте): один раз на профиль, --refresh-about повторяет.

    Не вышло: статус профиля не меняется, в примечание идёт пометка, about_fetched_at остаётся
    пустым (следующий запуск без --only-unprocessed попробует снова)."""
    if not getattr(args, "refresh_about", False) and await store.about_fetched(username):
        return
    about = await fetch_about(page, f"{IG}/{username}/")
    if about is None:
        res["note"] = join_notes(res["note"], "окно «Об аккаунте» не собрано")
        return
    await store.save_about(username, about["fields"], about["raw"])
    f = about["fields"]
    if f.get("is_kz") is False:
        res["note"] = join_notes(res["note"], "не Казахстан: is_target=false")
        log("    [+] Об аккаунте: не Казахстан → is_target=false")
        return
    if f.get("is_verified"):
        res["verified"] = True
    line = (
        f"    [+] Об аккаунте: с {f.get('joined_text') or '?'} | "
        f"{f.get('account_location') or 'местоположение не указано'}"
    )
    if f.get("is_verified"):
        line += f" | подтверждён {f.get('verified_text') or ''}".rstrip()
    log(line)


def join_notes(*notes: str | None) -> str:
    return "; ".join(n for n in notes if n)


async def _save_result(username, args, store, res, src, meta, raw) -> dict:
    """Пишет поля профиля в БД и дополняет результат."""
    if src != "api":
        res["note"] = join_notes(res["note"], f"данные со страницы: {src}")
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
