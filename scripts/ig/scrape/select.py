"""Какие профили парсить: ники и файл (заносятся в БД), --source, --only-unprocessed."""

import asyncpg

from ig.db.profiles import add_candidates, drop_processed, fetch_existing, list_targets
from ig.db.sources import Rejected, collapse, read_nick_file, to_candidates


async def select_profiles(
    conn: asyncpg.Connection, args, mode: str
) -> tuple[list[str], dict[str, int]]:
    """Профили запуска: (ники в порядке обработки, ник в нижнем регистре -> profiles.id).

    Ники из командной строки и из --file заносятся в БД (`source` cli/file), если их ещё нет;
    при --dry-run ничего не пишется. --source берёт профили из БД с is_target."""
    rejected = Rejected()
    explicit = []
    if args.nicks:
        explicit += to_candidates(args.nicks, "cli", None, rejected)
    if args.file:
        explicit += to_candidates(read_nick_file(args.file), "file", args.file.name, rejected)
    explicit = collapse(explicit)
    if explicit:
        await add_candidates(conn, explicit, dry_run=args.dry_run)
    rejected.print()

    names: dict[str, str] = {c.username: c.username for c in explicit}
    ids: dict[str, int] = {}
    if args.source:
        for pid, nick in await list_targets(conn, args.source):
            names.setdefault(nick.lower(), nick)
            ids[nick.lower()] = pid
    for nick, row in (await fetch_existing(conn, list(names), lock=False)).items():
        ids[nick] = row["id"]
    if args.only_unprocessed and ids:
        keep = set(await drop_processed(conn, list(ids.values()), mode))
        names = {n: v for n, v in names.items() if n not in ids or ids[n] in keep}
        ids = {n: i for n, i in ids.items() if i in keep}
    return list(names.values()), ids
