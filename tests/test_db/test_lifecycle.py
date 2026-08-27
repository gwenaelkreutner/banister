"""Startup lifecycle tests: data directory validation and the single-instance lock
(spec 003 FR-004, FR-005).
"""
from __future__ import annotations

import pytest

from app.core.exceptions import AnotherInstanceRunningError
from app.db.lifecycle import acquire_instance_lock, ensure_data_dir


def test_ensure_data_dir_creates_a_missing_directory(tmp_path, monkeypatch):
    from app.db import lifecycle

    target = tmp_path / "nested" / "data"
    monkeypatch.setattr(lifecycle.settings, "data_dir", target)

    assert not target.exists()
    ensure_data_dir()
    assert target.is_dir()


def test_ensure_data_dir_is_idempotent(tmp_path, monkeypatch):
    from app.db import lifecycle

    monkeypatch.setattr(lifecycle.settings, "data_dir", tmp_path / "data")
    ensure_data_dir()
    ensure_data_dir()  # second call must not raise


def test_second_instance_is_refused(tmp_path, monkeypatch):
    from app.db import lifecycle

    monkeypatch.setattr(lifecycle.settings, "data_dir", tmp_path)

    first = acquire_instance_lock()
    try:
        with pytest.raises(AnotherInstanceRunningError):
            acquire_instance_lock()
    finally:
        first.release()


def test_a_new_instance_succeeds_after_the_first_releases(tmp_path, monkeypatch):
    from app.db import lifecycle

    monkeypatch.setattr(lifecycle.settings, "data_dir", tmp_path)

    first = acquire_instance_lock()
    first.release()

    second = acquire_instance_lock()  # must not raise
    second.release()


def test_lock_message_names_the_directory(tmp_path, monkeypatch):
    from app.db import lifecycle

    monkeypatch.setattr(lifecycle.settings, "data_dir", tmp_path)

    first = acquire_instance_lock()
    try:
        with pytest.raises(AnotherInstanceRunningError) as exc_info:
            acquire_instance_lock()
        assert str(tmp_path) in str(exc_info.value)
    finally:
        first.release()
