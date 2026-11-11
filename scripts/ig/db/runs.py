"""Таблица runs: запись о запуске (импорт профилей, позже парсинг)."""

from decimal import Decimal

import asyncpg

from core.timeutil import parse_dt
from ig.db import accounts as accounts_repo


async def start_run(conn: asyncpg.Connection, kind: str, settings: dict) -> int:
    """Создаёт запись runs со статусом running, возвращает её id."""
    return await conn.fetchval(
        "INSERT INTO runs (kind, settings) VALUES ($1, $2) RETURNING id", kind, settings
    )


async def finish_run(
    conn: asyncpg.Connection, run_id: int, status: str, error: str | None = None
) -> None:
    """Закрывает запуск: ok | error | interrupted."""
    await conn.execute(
        "UPDATE runs SET status = $2, error = $3, finished_at = now() WHERE id = $1",
        run_id,
        status,
        error,
    )


def _num(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


async def save_profile_runs(
    conn: asyncpg.Connection, run_id: int, account: str, entries: list[dict], ids: dict[str, int]
) -> None:
    """По строке profile_runs на каждую запись profile_run_entry() аккаунта."""
    for e in entries:
        pid = ids.get((e["username"] or "").lower())
        if pid is None:
            continue
        p, c, m = (e.get(k) or {} for k in ("posts", "comments", "media"))
        await conn.execute(
            """
            INSERT INTO profile_runs (
                run_id, account, profile_id, status, mode, stop_reason, abort_kind, note,
                started_at, finished_at, duration_sec,
                posts_new, posts_refreshed, posts_unchanged, posts_skipped_old, posts_in_period,
                posts_newest, comments_new, media_new_files, media_new_images, media_new_videos,
                media_new_bytes, media_failed, pages_opened)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                    $21,$22,$23,$24)
            """,
            run_id,
            account,
            pid,
            e.get("status") or "error",
            e.get("mode"),
            e.get("stop_reason"),
            e.get("abort_kind"),
            e.get("note") or None,
            parse_dt(e.get("started_at")),
            parse_dt(e.get("finished_at")),
            _num(e.get("duration_sec")),
            p.get("new", 0) or 0,
            p.get("refreshed", 0) or 0,
            p.get("unchanged", 0) or 0,
            p.get("skipped_old", 0) or 0,
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
    ids: dict[str, int],
    interrupted: bool,
) -> None:
    """Закрывает запуск: итоги по аккаунтам, profile_runs, статус и время окончания.

    touched: аккаунт -> {"status", "error"}; stats: аккаунт -> {"e429", "eauth"};
    entries: аккаунт -> [profile_run_entry]; nicks: аккаунт -> найденный ник.
    Если ни один аккаунт не участвовал, запись запуска удаляется."""
    if not accounts:
        await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
        return
    errors: list[str] = []
    async with conn.transaction():
        for acc in accounts:
            msg, s = touched.get(acc) or {}, stats.get(acc) or {}
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
            await save_profile_runs(conn, run_id, acc, entries.get(acc, []), ids)
            await accounts_repo.mark_run(conn, acc, msg.get("status"), err, nicks.get(acc))
        if interrupted:
            status = "interrupted"
        elif len(errors) == len(accounts):
            status = "error"
        else:
            status = "ok"
        await finish_run(conn, run_id, status, errors[0] if status == "error" else None)
