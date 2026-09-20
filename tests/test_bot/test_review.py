"""`/review` — picker des 5 dernières séances puis synthèse immédiate (un seul mode).

Le LLM (generate_session_review) est monkeypatché comme run_chat l'est dans
test_chat_freestyle_publish.py — app/bot/routers/review.py l'importe localement dans
_run_review() pour rester monkeypatchable (même convention que app/bot/routers/chat.py).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.bot.routers.review import cb_review_pick, cmd_review
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

    async def _fake_review(ctx):
        calls.append(ctx)
        return "Synthèse générée."

    monkeypatch.setattr("app.llm.review.generate_session_review", _fake_review)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}")

    await cb_review_pick(callback, db_session, user)

    assert len(calls) == 1
    assert callback.message.sent[-1] == "Synthèse générée."
