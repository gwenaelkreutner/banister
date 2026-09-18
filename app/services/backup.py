"""Automatic local database backup — taken once at startup, right before migrations run.

Separate from `scripts/backup.py` (an operator's explicit, single-shot backup to an
arbitrary `--out` path, spec 003 FR-016/017, no rotation, refuses to overwrite): this one
is unattended, timestamped, self-pruning, and exists to cover a specific documented risk
(`CLAUDE.md`, section ATL/CTL/TSB migration note): a failed Alembic migration on SQLite
does not roll back automatically the way it would on PostgreSQL, and that risk was
previously "accepted as-is" for a single-user deployment rather than mitigated. Taking a
snapshot immediately before `run_migrations()` in `app/main.py`'s `lifespan` means a
broken migration is always recoverable by restoring the backup taken seconds earlier.

Uses the same `VACUUM INTO` mechanism as `scripts/backup.py` (research R6: a complete,
internally consistent snapshot without stopping the app or locking out writers beyond the
instant of the snapshot itself).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_FILENAME_FORMAT = "%Y%m%d_%H%M%S"


def sqlite_path_from_url(url: str) -> Path | None:
    """Returns the local SQLite file path for `url`, or `None` when it points at a
    different backend (e.g. PostgreSQL, used in tests / advanced deployments) — those
    manage their own backups, so the automatic mechanism silently does nothing rather
    than crashing startup over a database it was never meant to touch."""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix):])


def _list_backups(backups_dir: Path) -> list[Path]:
    """Sorted oldest-to-newest — the `backup_<timestamp>.db` naming sorts
    chronologically as plain strings, no need to parse the timestamp back out."""
    return sorted(backups_dir.glob("backup_*.db"))


def should_backup(backups_dir: Path, min_interval: timedelta) -> bool:
    """False when the most recent backup is younger than `min_interval` — guards
    against a burst of near-duplicate snapshots when the app restarts repeatedly in a
    short window (e.g. `--reload` during development), since a startup backup otherwise
    fires on every single restart."""
    existing = _list_backups(backups_dir)
    if not existing:
        return True
    newest = existing[-1]
    age = datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)
    return age >= min_interval


def create_backup(db_path: Path, backups_dir: Path) -> Path:
    """Snapshots `db_path` into `backups_dir` with a timestamped filename. Caller must
    ensure `db_path` exists — there is nothing to back up before the database has ever
    been created (fresh install, first startup, migrations haven't run yet)."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    out_path = backups_dir / f"backup_{datetime.now():{_FILENAME_FORMAT}}.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM INTO ?", (str(out_path),))
    finally:
        conn.close()
    return out_path


def rotate_backups(backups_dir: Path, keep: int) -> list[Path]:
    """Deletes all but the `keep` most recent backups. Returns the deleted paths."""
    existing = _list_backups(backups_dir)
    to_delete = existing[:-keep] if keep > 0 else existing
    for path in to_delete:
        path.unlink()
    return to_delete


def run_startup_backup(
    database_url: str,
    data_dir: Path,
    *,
    keep: int = 7,
    min_interval: timedelta = timedelta(hours=20),
) -> Path | None:
    """Entry point called from `lifespan`, before migrations. Synchronous and
    blocking (sqlite3 has no async driver) — caller wraps it in `asyncio.to_thread`.
    Never raises: a backup failure must not block the app from starting, since the
    thing it protects against (a bad migration) hasn't happened yet at this point —
    logs and returns `None` on any problem instead."""
    db_path = sqlite_path_from_url(database_url)
    if db_path is None:
        return None  # Non-SQLite backend — not this mechanism's job.
    if not db_path.is_file():
        return None  # Nothing to back up yet (fresh install, pre-first-migration).

    backups_dir = data_dir / "backups"
    if not should_backup(backups_dir, min_interval):
        logger.debug("Backup automatique ignoré — le dernier date de moins de %s.", min_interval)
        return None

    try:
        out_path = create_backup(db_path, backups_dir)
        deleted = rotate_backups(backups_dir, keep)
    except Exception:
        logger.exception("Échec du backup automatique — démarrage non bloqué.")
        return None

    logger.info(
        "Backup automatique : '%s' (%d octets)%s",
        out_path, out_path.stat().st_size,
        f" — {len(deleted)} ancien(s) supprimé(s)" if deleted else "",
    )
    return out_path
