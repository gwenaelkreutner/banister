"""`/review` — picker des 5 dernières séances + picker de profondeur (plan révisé).

Le LLM (generate_session_review) est monkeypatché comme run_chat l'est dans
test_chat_freestyle_publish.py — app/bot/routers/review.py l'importe localement dans
_run_review() pour rester monkeypatchable (même convention que app/bot/routers/chat.py).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.bot.routers.review import cb_review_depth, cb_review_pick, cmd_review
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


async def test_review_cli_depth_brief_is_carried_in_the_picker_callback_data(db_session):
    user = await _make_user(db_session)
    await _make_log(db_session, user.id, days_ago=1)
    msg = _Msg(text="/review brief")

    await cmd_review(msg, db_session, user)

    kb = msg.markups[-1]
    assert kb.inline_keyboard[0][0].callback_data.endswith(":brief")


async def test_review_invalid_cli_arg_falls_back_to_no_depth(db_session):
    user = await _make_user(db_session)
    await _make_log(db_session, user.id, days_ago=1)
    msg = _Msg(text="/review n'importe quoi")

    await cmd_review(msg, db_session, user)

    kb = msg.markups[-1]
    assert kb.inline_keyboard[0][0].callback_data.endswith(":none")


# ── cb_review_pick ───────────────────────────────────────────────────────────


async def test_pick_unknown_log_shows_alert_without_crashing(db_session):
    import uuid

    user = await _make_user(db_session)
    callback = _Callback(f"review:pick:{uuid.uuid4().hex}:none")

    await cb_review_pick(callback, db_session, user)

    assert callback.alerts
    assert "plus disponible" in callback.alerts[0]


async def test_pick_another_users_log_is_rejected(db_session):
    user = await _make_user(db_session, telegram_id=1)
    other = await _make_user(db_session, telegram_id=2)
    log = await _make_log(db_session, other.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}:none")

    await cb_review_pick(callback, db_session, user)

    assert callback.alerts


async def test_pick_with_cli_depth_skips_the_depth_picker(db_session, monkeypatch):
    calls = []

    async def _fake_review(ctx, depth):
        calls.append(depth)
        return "Synthèse générée."

    monkeypatch.setattr("app.llm.review.generate_session_review", _fake_review)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}:brief")

    await cb_review_pick(callback, db_session, user)

    assert calls == ["brief"]
    assert callback.message.sent[-1] == "Synthèse générée."


async def test_pick_without_cli_depth_shows_the_depth_picker(db_session, monkeypatch):
    calls = []

    async def _fake_review(ctx, depth):
        calls.append(depth)
        return "Synthèse générée."

    monkeypatch.setattr("app.llm.review.generate_session_review", _fake_review)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)
    callback = _Callback(f"review:pick:{log.id.hex}:none")

    await cb_review_pick(callback, db_session, user)

    assert calls == []  # pas encore de synthèse — on attend le choix de profondeur
    assert "profondeur" in callback.message.sent[-1].lower()
    kb = callback.message.markups[-1]
    assert len(kb.inline_keyboard[0]) == 3  # brief / default / deep
    for button in kb.inline_keyboard[0]:
        assert button.callback_data.startswith(f"review:depth:{log.id.hex}:")


# ── cb_review_depth ──────────────────────────────────────────────────────────


async def test_depth_callback_runs_the_review_at_the_chosen_depth(db_session, monkeypatch):
    calls = []

    async def _fake_review(ctx, depth):
        calls.append(depth)
        return "Synthèse profonde."

    monkeypatch.setattr("app.llm.review.generate_session_review", _fake_review)

    user = await _make_user(db_session)
    log = await _make_log(db_session, user.id, days_ago=1)
    callback = _Callback(f"review:depth:{log.id.hex}:deep")

    await cb_review_depth(callback, db_session, user)

    assert calls == ["deep"]
    assert callback.message.sent[-1] == "Synthèse profonde."


async def test_depth_callback_unknown_log_shows_alert(db_session):
    import uuid

    user = await _make_user(db_session)
    callback = _Callback(f"review:depth:{uuid.uuid4().hex}:deep")

    await cb_review_depth(callback, db_session, user)

    assert callback.alerts
