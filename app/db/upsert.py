"""Dialect-neutral upsert helper (spec 003 research R4).

Three repositories need an `INSERT ... ON CONFLICT` construct. The construct itself is
dialect-specific to import (`sqlalchemy.dialects.postgresql.insert` vs
`sqlalchemy.dialects.sqlite.insert`), even though the resulting SQL and semantics are the
same on both backends. This selects the right one from the session's bound engine so call
sites stay backend-agnostic.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


def dialect_insert(session: AsyncSession):
    """Return the `insert()` construct for the dialect this session is bound to."""
    dialect_name = session.bind.dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise NotImplementedError(f"No upsert construct wired for dialect {dialect_name!r}")
    return insert
