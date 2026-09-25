"""Telegram RPE is pushed to the matching Intervals.icu activity."""

import uuid
from types import SimpleNamespace

import pytest

from app.bot.routers import session_log


async def test_sync_rpe_uses_source_activity_and_value(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, api_key, *, athlete_id):
            pass

        async def update_activity_rpe(self, activity_id, rpe):
            calls.append((activity_id, rpe))

    monkeypatch.setattr(session_log, "IntervalsClient", FakeClient)

    assert await session_log._sync_rpe_to_intervals("i123", 8.0)
    assert calls == [("i123", 8)]


async def test_sync_rpe_reports_failure_without_raising(monkeypatch):
    class FailingClient:
        def __init__(self, api_key, *, athlete_id):
            pass

        async def update_activity_rpe(self, activity_id, rpe):
            raise RuntimeError("unavailable")

    monkeypatch.setattr(session_log, "IntervalsClient", FailingClient)

    assert not await session_log._sync_rpe_to_intervals("i123", 8.0)


@pytest.mark.parametrize(
    ("token", "expected_rpe", "sync_succeeds", "expected_warning"),
    [
        ("1", 1.0, True, False),
        ("8", 8.0, True, False),
        ("10", 10.0, True, False),
        ("skip", None, True, False),
        ("8", 8.0, False, True),
    ],
)
async def test_callback_pushes_only_entered_rpe(
    monkeypatch, token, expected_rpe, sync_succeeds, expected_warning
):
    log_id = uuid.uuid4()
    log = SimpleNamespace(id=log_id, user_id=1, rpe=None, source_activity_id="i123")
    synced = []
    warnings = []
    edited = []

    async def get_by_id(session, requested_id):
        assert requested_id == log_id
        return log

    async def sync(activity_id, rpe):
        synced.append((activity_id, rpe))
        return sync_succeeds

    async def record_warning(text):
        warnings.append(text)

    async def edit_text(text, **kwargs):
        edited.append(text)

    async def stop_after_sync(*args):
        raise StopAfterSync

    class StopAfterSync(Exception):
        pass

    monkeypatch.setattr(session_log.repo.session_log_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(session_log.repo.session_log_repo, "get_all_for_user", stop_after_sync)
    monkeypatch.setattr(session_log, "_sync_rpe_to_intervals", sync)
    callback = SimpleNamespace(
        data=f"log:rpe:{log_id}:{token}",
        message=SimpleNamespace(edit_text=edit_text, answer=record_warning),
        answer=stop_callback_answer,
    )

    with pytest.raises(StopAfterSync):
        await session_log.cb_rpe(callback, None, SimpleNamespace(id=1))

    assert log.rpe == expected_rpe
    assert synced == ([("i123", expected_rpe)] if expected_rpe is not None else [])
    assert any("transfert" in text for text in warnings) == expected_warning
    expected_text = f"{int(expected_rpe)}/10" if expected_rpe is not None else "Ressenti passé"
    assert expected_text in edited[0]


async def stop_callback_answer():
    pass


@pytest.mark.parametrize("token", ["0", "11", "abc"])
async def test_callback_rejects_out_of_scale_rpe(monkeypatch, token):
    log_id = uuid.uuid4()
    log = SimpleNamespace(id=log_id, user_id=1, rpe=None)
    answers = []

    async def get_by_id(session, requested_id):
        return log

    async def answer(text, *, show_alert):
        answers.append((text, show_alert))

    monkeypatch.setattr(session_log.repo.session_log_repo, "get_by_id", get_by_id)
    callback = SimpleNamespace(data=f"log:rpe:{log_id}:{token}", answer=answer)

    await session_log.cb_rpe(callback, None, SimpleNamespace(id=1))

    assert log.rpe is None
    assert answers == [("Choisis une note de 1 à 10.", True)]
