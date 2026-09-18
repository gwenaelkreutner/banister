"""Tests for the automatic startup backup (app/services/backup.py).

Distinct from tests/test_db/test_backup.py, which exercises the raw VACUUM INTO
mechanism used by the operator-facing scripts/backup.py CLI. These tests cover the
scheduling/rotation/throttle logic layered on top for the unattended startup path.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from app.services.backup import (
    create_backup,
    rotate_backups,
    run_startup_backup,
    should_backup,
    sqlite_path_from_url,
)


def _make_sqlite_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def test_sqlite_path_from_url_extracts_local_path():
    assert sqlite_path_from_url("sqlite+aiosqlite:///data/banister.db") == Path("data/banister.db")


def test_sqlite_path_from_url_returns_none_for_other_backends():
    assert sqlite_path_from_url("postgresql+asyncpg://user:pw@host/db") is None


def test_create_backup_writes_a_timestamped_valid_snapshot(tmp_path):
    db_path = tmp_path / "banister.db"
    _make_sqlite_db(db_path)
    backups_dir = tmp_path / "backups"

    out_path = create_backup(db_path, backups_dir)

    assert out_path.parent == backups_dir
    assert out_path.name.startswith("backup_") and out_path.name.endswith(".db")
    check = sqlite3.connect(str(out_path))
    assert check.execute("SELECT name FROM sqlite_master WHERE name='t'").fetchone()
    check.close()


def test_rotate_backups_keeps_only_the_n_most_recent(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    paths = []
    for i in range(5):
        p = backups_dir / f"backup_2026010{i}_000000.db"
        p.write_bytes(b"x")
        paths.append(p)
        time.sleep(0.01)

    deleted = rotate_backups(backups_dir, keep=2)

    remaining = sorted(backups_dir.glob("backup_*.db"))
    assert remaining == paths[-2:]
    assert set(deleted) == set(paths[:-2])


def test_rotate_backups_no_op_when_under_the_limit(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "backup_20260101_000000.db").write_bytes(b"x")

    deleted = rotate_backups(backups_dir, keep=7)

    assert deleted == []
    assert len(list(backups_dir.glob("backup_*.db"))) == 1


def test_should_backup_true_when_no_backup_exists_yet(tmp_path):
    backups_dir = tmp_path / "backups"
    assert should_backup(backups_dir, timedelta(hours=20)) is True


def test_should_backup_false_when_last_one_is_recent(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "backup_20260101_000000.db").write_bytes(b"x")

    assert should_backup(backups_dir, timedelta(hours=20)) is False


def test_should_backup_true_when_last_one_is_old(tmp_path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    old = backups_dir / "backup_20200101_000000.db"
    old.write_bytes(b"x")
    old_time = time.time() - timedelta(hours=48).total_seconds()
    os.utime(old, (old_time, old_time))

    assert should_backup(backups_dir, timedelta(hours=20)) is True


def test_run_startup_backup_skips_non_sqlite_backend(tmp_path):
    result = run_startup_backup("postgresql+asyncpg://u:p@h/db", tmp_path)
    assert result is None
    assert not (tmp_path / "backups").exists()


def test_run_startup_backup_skips_when_db_does_not_exist_yet(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'banister.db').as_posix()}"
    result = run_startup_backup(url, tmp_path)
    assert result is None
    assert not (tmp_path / "backups").exists()


def test_run_startup_backup_creates_and_rotates(tmp_path):
    db_path = tmp_path / "banister.db"
    _make_sqlite_db(db_path)
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    result = run_startup_backup(url, tmp_path, keep=1, min_interval=timedelta(0))

    assert result is not None
    assert result.exists()
    assert len(list((tmp_path / "backups").glob("backup_*.db"))) == 1


def test_run_startup_backup_never_raises_on_internal_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "banister.db"
    _make_sqlite_db(db_path)
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.services.backup.create_backup", _boom)

    result = run_startup_backup(url, tmp_path, min_interval=timedelta(0))

    assert result is None
