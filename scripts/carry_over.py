#!/usr/bin/env python3
"""One-time carry-over of locally originated data from the hosted PostgreSQL database to
the local SQLite store — spec 003 FR-023..026.

Usage:
    uv run python scripts/carry_over.py --from "$POSTGRES_URL" --to ./data/banister.db --dry-run
    uv run python scripts/carry_over.py --from "$POSTGRES_URL" --to ./data/banister.db

What is carried, and what is not (spec 003 data-model.md, "locally originated data"):

    CARRIED   users, athlete_profiles, training_plans, session_logs, chat_messages,
              weekly_adherence, oauth_connections — produced by this application, exist
              nowhere else, irreplaceable if lost.

    SKIPPED   activities (FR-024) — sourced from the training data provider, which is
              authoritative for it; re-fetched rather than migrated, so transferring it
              would duplicate work a re-fetch does more reliably.

Table list is a fixed ALLOWLIST rather than "copy everything except X" (FR-026): a table
this script doesn't know about — including one already obsolete in the live database but
never dropped there, such as a leftover from before this project's own schema cleanup —
is automatically excluded without needing to be named. At the time this script was
written, every table in the allowlist above is still live and in current use (in
particular oauth_connections: the previous sport-data provider has not yet been replaced,
so its authorization records are not yet obsolete — spec 002 removes that table later, not
this script).

Never runs against the live source: only reads from --from, only writes to --to. The
source is untouched under every code path, including failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.db.models import (  # noqa: E402
    AthleteProfile,
    Base,
    ChatMessage,
    OAuthConnection,
    SessionLog,
    TrainingPlan,
    User,
    WeeklyAdherence,
)

# Order matters: parents before the children that reference them.
CARRIED_MODELS = [
    User,
    AthleteProfile,
    TrainingPlan,
    SessionLog,
    ChatMessage,
    WeeklyAdherence,
    OAuthConnection,
]

SKIPPED_TABLES = {
    "activities": "re-fetched from the training data source (FR-024), not transferred",
}


async def _row_count(engine, table) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(sa.func.count()).select_from(table))
        return result.scalar_one()


async def _fetch_all_rows(engine, table) -> list[dict]:
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(table))
        return [dict(row._mapping) for row in result]


async def carry_over(from_url: str, to_url: str, *, dry_run: bool) -> None:
    source_engine = create_async_engine(from_url)
    dest_engine = create_async_engine(to_url)

    try:
        plan = []
        for model in CARRIED_MODELS:
            table = model.__table__
            count = await _row_count(source_engine, table)
            plan.append((model.__name__, table.name, count))

        print("Carry-over plan:")
        for name, table_name, count in plan:
            print(f"  {name:20s} ({table_name}): {count} row(s)")
        for table_name, reason in SKIPPED_TABLES.items():
            print(f"  {'(skipped)':20s} ({table_name}): {reason}")

        if dry_run:
            print("\n--dry-run: nothing written. Source and destination both untouched.")
            return

        # Schema creation happens only on a real run — a --dry-run must leave no trace
        # at the destination, not even an empty schema, so the plan it prints can be
        # trusted to describe exactly what a real run would do and nothing more.
        async with dest_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        for model in CARRIED_MODELS:
            table = model.__table__
            rows = await _fetch_all_rows(source_engine, table)
            if not rows:
                continue
            async with dest_engine.begin() as conn:
                # Idempotent / retryable (FR-025): ignore rows that already exist at the
                # destination from a prior partial run, rather than failing or duplicating.
                stmt = _dialect_insert(dest_engine)(table).values(rows)
                stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                result = await conn.execute(stmt)
            inserted = result.rowcount if result.rowcount is not None else len(rows)
            skipped = len(rows) - inserted
            note = f" ({skipped} already present, skipped)" if skipped else ""
            print(f"  Carried {inserted}/{len(rows)} row(s) into {table.name}.{note}")

        print("\nDone. Source database was not modified.")

    finally:
        await source_engine.dispose()
        await dest_engine.dispose()


def _dialect_insert(engine):
    """Same logic as app/db/upsert.dialect_insert, adapted for an Engine rather than an
    AsyncSession — this script works with raw connections, not ORM sessions."""
    dialect_name = engine.dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise NotImplementedError(f"No upsert construct wired for dialect {dialect_name!r}")
    return insert


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="from_url", required=True,
                         help="Source PostgreSQL URL (read-only; never modified).")
    parser.add_argument("--to", dest="to_url", required=True,
                         help="Destination SQLite path or sqlite+aiosqlite:/// URL.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Report what would be carried without writing anything.")
    args = parser.parse_args()

    to_url = args.to_url
    if not to_url.startswith("sqlite"):
        to_url = f"sqlite+aiosqlite:///{to_url}"

    import asyncio
    asyncio.run(carry_over(args.from_url, to_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
