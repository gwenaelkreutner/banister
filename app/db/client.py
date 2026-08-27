from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.resolved_database_url,
    # AsyncAdaptedQueuePool is SQLAlchemy's default for both asyncpg and file-based
    # aiosqlite (verified empirically — spec 003 T029/T032), so these kwargs need no
    # per-dialect branching: the same pool class backs both backends.
    pool_size=3,
    max_overflow=5,
    pool_pre_ping=True,
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _) -> None:
    """Applied per-connection, not once at startup: pragmas are per-connection state in
    SQLite, and pooled connections would otherwise each start with the defaults. Only
    fires for SQLite — PostgreSQL connections don't carry a sqlite3-style `execute` here.

    - foreign_keys=ON: SQLite does not enforce declared foreign keys unless told to, and
      when it doesn't, it fails silently — a parent delete leaves orphaned children with
      no error (spec 003 research R1, pinned by tests/test_db/test_cascade.py).
    - journal_mode=WAL: lets readers proceed while a write is in progress, instead of
      SQLite's default of blocking them — several background schedulers share one writer
      with interactive handlers (spec 003 research R5).
    - busy_timeout: makes a writer that finds the database briefly locked wait and retry
      inside the driver, rather than the athlete seeing a "database is locked" error
      (spec 003 FR-014).
    - synchronous=NORMAL: the standard pairing with WAL — safe against application or OS
      crashes, only trading off durability against a full power loss, which is an
      acceptable posture for a single-athlete self-hosted deployment.
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session
