"""Запуски парсера: таблицы runs, run_accounts, profile_runs."""

from datetime import datetime
from decimal import Decimal

import asyncpg

from config import TH
from core.timeutil import parse_dt
from db import accounts as accounts_repo


async def start_run(conn: asyncpg.Connection, kind: str, settings: dict) -> int:
    """Новая запись runs со статусом running. kind: profiles | search | classify (discover остался в CHECK ради старых строк)."""
    return await conn.fetchval(
        "INSERT INTO runs (kind, settings) VALUES ($1, $2) RETURNING id", kind, settings
    )


async def ensure_profile(
    conn: asyncpg.Connection, username: str, scraped_at: datetime | None = None
) -> int:
    """id профиля (регистр ника не важен); заводит строку, если её нет.

    scraped_at, если передан, обновляет last_scraped_at. Остальные поля профиля
    (og_*, title) заполняются при обработке профиля.
    """
    return await conn.fetchval(
        """
        INSERT INTO profiles (username, url) VALUES ($1, $2)
        ON CONFLICT ((lower(username))) DO UPDATE
            SET last_scraped_at = coalesce($3::timestamptz, profiles.last_scraped_at)
        RETURNING id
        """,
        username,
        f"{TH}/@{username}",
        scraped_at,
    )


async def finish_run(
    conn: asyncpg.Connection,
    run_id: int,
    status: str,
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    """Закрывает запуск без аккаунтов (classify): итоги дописываются в settings.

    status: ok | error | interrupted. Для запусков с аккаунтами есть finalize_run.
    """
    await conn.execute(
        "UPDATE runs SET status = $2, finished_at = now(), "
        "settings = settings || $3::jsonb, error = $4 WHERE id = $1",
        run_id,
        status,
        summary or {},
        error,
    )


def _num(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


async def save_profile_runs(
    conn: asyncpg.Connection, run_id: int, account: str, entries: list[dict]
) -> None:
    """По строке profile_runs на каждую запись profile_run_entry() аккаунта."""
    for e in entries:
        finished = parse_dt(e.get("finished_at"))
        ok = e.get("status") in ("ok", "empty")
        profile_id = await ensure_profile(conn, e["username"], finished if ok else None)
        p, c, m = (e.get(k) or {} for k in ("posts", "comments", "media"))
        await conn.execute(
            """
            INSERT INTO profile_runs (
                run_id, account, profile_id, status, mode, stop_reason, abort_kind, note,
                started_at, finished_at, duration_sec,
                posts_new, posts_refreshed, posts_unchanged, posts_skipped_old,
                posts_skipped_video, posts_in_period, posts_newest,
                comments_new, media_new_files, media_new_images, media_new_videos,
                media_new_bytes, media_failed, pages_opened)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                    $19,$20,$21,$22,$23,$24,$25)
            """,
            run_id,
            account,
            profile_id,
            e.get("status") or "error",
            e.get("mode"),
            e.get("stop_reason"),
            e.get("abort_kind"),
            e.get("note") or None,
            parse_dt(e.get("started_at")),
            finished,
            _num(e.get("duration_sec")),
            p.get("new", 0) or 0,
            p.get("refreshed", 0) or 0,
            p.get("unchanged", 0) or 0,
            p.get("skipped_old", 0) or 0,
            p.get("skipped_video", 0) or 0,
            p.get("in_period", 0) or 0,
            parse_dt(p.get("newest")),
            c.get("new", 0) or 0,
            m.get("new_files", 0) or 0,
            m.get("new_images", 0) or 0,
            m.get("new_videos", 0) or 0,
            m.get("new_bytes", 0) or 0,
            m.get("download_failed", 0) or 0,
            e.get("pages_opened", 0) or 0,
        )


async def finalize_run(
    conn: asyncpg.Connection,
    run_id: int,
    accounts: list[str],
    touched: dict,
    stats: dict,
    entries: dict,
    nicks: dict,
    interrupted: bool,
) -> None:
    """Закрывает запуск: итоги по аккаунтам, записи профилей, статус и время окончания.

    touched: аккаунт -> {"status", "error"}; stats: аккаунт -> {"e429", "eauth"};
    entries: аккаунт -> [profile_run_entry]; nicks: аккаунт -> найденный ник.
    Если ни один аккаунт не участвовал (всё уже было сделано), запись запуска удаляется.
    """
    if not accounts:
        await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
        return
    errors: list[str] = []
    async with conn.transaction():
        for acc in accounts:
            msg = touched.get(acc) or {}
            s = stats.get(acc) or {}
            err = msg.get("error")
            if err:
                errors.append(err)
            await conn.execute(
                """
                INSERT INTO run_accounts (run_id, account, status, error, errors_429, errors_auth)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (run_id, account) DO UPDATE SET
                    status = EXCLUDED.status, error = EXCLUDED.error,
                    errors_429 = EXCLUDED.errors_429, errors_auth = EXCLUDED.errors_auth
                """,
                run_id,
                acc,
                "error" if err else "ok",
                err,
                s.get("e429", 0),
                s.get("eauth", 0),
            )
            await save_profile_runs(conn, run_id, acc, entries.get(acc, []))
            await accounts_repo.mark_run(conn, acc, msg.get("status"), err, nicks.get(acc))
        if interrupted:
            status = "interrupted"
        elif len(errors) == len(accounts):  # ни один аккаунт не отработал без ошибки
            status = "error"
        else:
            status = "ok"
        await conn.execute(
            "UPDATE runs SET status = $2, finished_at = now(), error = $3 WHERE id = $1",
            run_id,
            status,
            errors[0] if status == "error" else None,
        )
