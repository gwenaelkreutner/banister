"""Upsert helper (spec 003 research R4, T052 cutover).

Three repositories need an `INSERT ... ON CONFLICT` construct. This project targets SQLite
only since the Phase 7 cutover — the PostgreSQL branch this helper carried during the
portability port has been removed rather than left dead, per T052's requirement that no
PostgreSQL-specific import remain in app/.
"""
from __future__ import annotations

from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession


def dialect_insert(session: AsyncSession):
    """Return the `insert()` construct for the dialect this session is bound to."""
    dialect_name = session.bind.dialect.name
    if dialect_name != "sqlite":
        raise NotImplementedError(
            f"No upsert construct wired for dialect {dialect_name!r} — this project "
            f"targets SQLite only."
        )
    return _sqlite_insert
