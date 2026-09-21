"""`/review` — picker des 5 dernières séances puis synthèse immédiate (un seul mode).

Le LLM (generate_session_review) est monkeypatché comme run_chat l'est dans
test_chat_freestyle_publish.py — app/bot/routers/review.py l'importe localement dans
_run_review() pour rester monkeypatchable (même convention que app/bot/routers/chat.py).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.bot.routers.review import (
    _backfill_rpe_from_source,
    _fetch_dfa,
    cb_review_pick,
    cmd_review,
)
from app.db.models.session_log import SessionLog
from app.db.models.user import User


class _Msg:
    def __init__(self, text: str | None = None):
        self.text = text
        self.sent: list[str] = []
        self.markups: list = []

    async def answer(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))

    async def edit_text(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))


class _Callback:
    def __init__(self, data: str):
        self.data = data
        self.message = _Msg()
        self.answered = 0
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answered += 1
        if show_alert:
            self.alerts.append(text)


async def _make_user(session, telegram_id: int = 1, onboarded: bool = True) -> User:
    from datetime import UTC, datetime

    user = User(
        telegram_id=telegram_id, first_name="Gwen",
        onboarding_completed_at=datetime.now(UTC) if onboarded else None,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_log(session, user_id, days_ago: int, **kw) -> SessionLog:
    log = SessionLog(
        user_id=user_id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today() - timedelta(days=days_ago), status="done",
        tss_actual=50.0, **kw,
    )
    session.add(log)
    await session.flush()
    return log


# ── cmd_review — le picker de séance ────────────────────────────────────────


async def test_review_with_no_logs_tells_the_athlete(db_session):
    user = await _make_user(db_session)
    msg = _Msg(text="/review")

    await cmd_review(msg, db_session, user)

    assert "Aucune séance loggée" in msg.sent[-1]
    assert msg.markups[-1] is None


async def test_review_shows_at_most_five_most_recent_first(db_session):
    user = await _make_user(db_session)
    for days_ago in [10, 1, 5, 3, 8, 2, 7]:  # 7 logs, le picker n'en montre que 5
        await _make_log(db_session, user.id, days_ago)
    msg = _Msg(text="/review")

    await cmd_review(msg, db_session, user)

    kb = msg.markups[-1]
    buttons = [row[0] for row in kb.inline_keyboard]
    assert len(buttons) == 5
    # le plus récent (1 jour) doit être le premier bouton
    assert buttons[0].callback_data.startswith("review:pick:")


async def test_review_blocks_before_onboarding(db_session):
    user = await _make_user(db_session, onboarded=False)
    msg = _Msg(text="/review")

    await cmd_review(msg, db_session, user)

    assert "/setup" in msg.sent[-1]


# ── cb_review_pick ───────────────────────────────────────────────────────────


async def test_pick_unknown_log_shows_alert_without_crashing(db_session):
    import uuid

    user = await _make_user(db_session)
    callback = _Callback(f"review:pick:{uuid.uuid4().hex}")

    await cb_review_pick(callback, db_session, user)

    assert callback.alerts
    assert "plus disponible" in callback.alerts[0]


async def test_pick_another_users_log_is_rejected(db_session):
    user = await _make_user(db_session, telegram_id=1)
    other = await _make_user(db_session, telegram_id=2)
    log = await _make_log(db_session, other.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}")

    await cb_review_pick(callback, db_session, user)

    assert callback.alerts


async def test_pick_runs_the_review_immediately(db_session, monkeypatch):
    calls = []

    async def _fake_review(ctx, dfa=None):
        calls.append(ctx)
        return "Synthèse générée."

    monkeypatch.setattr("app.llm.review.generate_session_review", _fake_review)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}")

    await cb_review_pick(callback, db_session, user)

    assert len(calls) == 1
    assert callback.message.sent[-1] == "Synthèse générée."


# ── _fetch_dfa (2026-09-21) — câblage réseau best-effort ─────────────────────


class _FakeStreamsClient:
    def __init__(self, raw_streams=None, raise_error=False):
        self._raw_streams = raw_streams or []
        self._raise_error = raise_error
        self.calls = []

    async def get_activity_streams(self, activity_id, *, types):
        self.calls.append((activity_id, types))
        if self._raise_error:
            raise RuntimeError("boom")
        return self._raw_streams


async def test_fetch_dfa_returns_none_without_source_activity_id(db_session):
    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)  # source_activity_id absent
    assert await _fetch_dfa(log) is None


async def test_fetch_dfa_returns_none_on_network_error(db_session, monkeypatch):
    fake_client = _FakeStreamsClient(raise_error=True)
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake_client)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    assert await _fetch_dfa(log) is None
    assert fake_client.calls  # a bien tenté l'appel


async def test_fetch_dfa_returns_none_when_no_alphahrv_recording(db_session, monkeypatch):
    # dfa_a1 absent des streams — état normal pour un compte sans AlphaHRV.
    fake_client = _FakeStreamsClient(raw_streams=[
        {"type": "time", "data": [0, 1, 2]},
        {"type": "watts", "data": [100, 110, 120]},
    ])
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake_client)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    assert await _fetch_dfa(log) is None
    assert fake_client.calls[0] == ("i123", ["dfa_a1", "artifacts", "heartrate", "watts"])


async def test_fetch_dfa_computes_block_when_alphahrv_data_present(db_session, monkeypatch):
    from app.engine.dfa import DFA_MIN_DURATION_SECS

    n = DFA_MIN_DURATION_SECS + 50
    fake_client = _FakeStreamsClient(raw_streams=[
        {"type": "dfa_a1", "data": [0.9] * n},
    ])
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake_client)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    result = await _fetch_dfa(log)
    assert result is not None
    assert result.quality.sufficient is True
    assert result.avg == 0.9


# ── _backfill_rpe_from_source (2026-09-21) — rattrapage RPE depuis intervals.icu ─────


class _FakeActivityClient:
    def __init__(self, activity: dict):
        self._activity = activity
        self.calls: list[str] = []

    async def get_activity(self, activity_id, **kw):
        self.calls.append(activity_id)
        return self._activity


async def test_backfill_does_nothing_when_rpe_already_set(db_session, monkeypatch):
    fake = _FakeActivityClient({"icu_rpe": 9})
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake)

    user = await _make_user(db_session)
    log = await _make_log(
        db_session, user.id, days_ago=1, source_activity_id="i123", rpe_emoji="easy"
    )

    await _backfill_rpe_from_source(log)

    assert log.rpe_emoji == "easy"  # Telegram (déjà répondu) l'emporte
    assert fake.calls == []  # jamais appelé — pas la peine


async def test_backfill_does_nothing_without_source_activity_id(db_session, monkeypatch):
    fake = _FakeActivityClient({"icu_rpe": 9})
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)  # source_activity_id absent

    await _backfill_rpe_from_source(log)

    assert log.rpe_emoji is None
    assert fake.calls == []


async def test_backfill_fills_rpe_from_icu_rpe_when_empty(db_session, monkeypatch):
    fake = _FakeActivityClient({"icu_rpe": 8})  # >= RPE_HARD_MIN
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    await _backfill_rpe_from_source(log)

    assert log.rpe_emoji == "hard"
    assert fake.calls == ["i123"]


async def test_backfill_leaves_rpe_none_when_source_has_no_value(db_session, monkeypatch):
    fake = _FakeActivityClient({"icu_rpe": None})
    monkeypatch.setattr("app.bot.routers.review._client", lambda: fake)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    await _backfill_rpe_from_source(log)

    assert log.rpe_emoji is None


async def test_backfill_swallows_network_errors(db_session, monkeypatch):
    class _RaisingClient:
        async def get_activity(self, activity_id, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.bot.routers.review._client", lambda: _RaisingClient())

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1, source_activity_id="i123")

    await _backfill_rpe_from_source(log)  # ne lève pas

    assert log.rpe_emoji is None
