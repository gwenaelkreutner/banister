#!/usr/bin/env python3
"""Back up the local database to a single file — spec 003 FR-016, FR-017.

Usage:
    uv run python scripts/backup.py --out ./backup.db

Restore is copying the file back:
    docker compose down
    cp ./backup.db ./data/banister.db
    docker compose up -d

No database client, no export/import step, no expertise beyond "copy a file" — that is
the whole point (spec 003 SC-005: an operator with no database expertise completes a
backup and a full restore using only the documentation).

How it works: SQLite's `VACUUM INTO` writes a complete, internally consistent snapshot
of the live database to a new file without requiring the application to stop and without
locking out other readers or writers for more than the instant of the snapshot itself
(research R6). This is why the documented restore procedure above is a plain file copy —
the backup file already IS a valid, complete database.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# scripts/ sits next to app/, not under it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402


def _sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise SystemExit(
            f"This backup tool only supports the local SQLite database. Current "
            f"configuration resolves to a different backend ({url.split(':', 1)[0]}). "
            f"For a hosted database, use that database's own backup tooling instead."
        )
    return Path(url[len(prefix):])


def backup(out_path: Path) -> None:
    db_path = _sqlite_path_from_url(settings.resolved_database_url)
    if not db_path.is_file():
        raise SystemExit(f"No database found at '{db_path}' — nothing to back up.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        raise SystemExit(
            f"'{out_path}' already exists — refusing to overwrite it. "
            f"Choose a different --out path."
        )

    conn = sqlite3.connect(str(db_path))
    try:
        # VACUUM INTO takes its own snapshot; it does not require app.db.client's
        # foreign_keys/WAL pragmas to be set on THIS connection, since it operates on
        # the source file's committed state directly.
        conn.execute("VACUUM INTO ?", (str(out_path),))
    finally:
        conn.close()

    print(f"Backup written to '{out_path}' ({out_path.stat().st_size:,} bytes).")
    print("Restore: stop the app, copy this file over your data directory's "
          "banister.db, then start the app again.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", required=True, type=Path,
        help="Path to write the backup to (must not already exist).",
    )
    args = parser.parse_args()
    backup(args.out)


if __name__ == "__main__":
    main()
