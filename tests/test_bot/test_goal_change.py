"""Changing the goal does not erase the athlete (spec 007 US3, FR-011..FR-016, SC-005).

`parse_goal_date` is pure. `_regenerate` needs a populated DB — a user with an active
plan, a profile, logged sessions, adherence, chat.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.bot.routers.goal import (
    _enter_freestyle_mode,
    _plan_change_preview,
    _regenerate,
    cmd_goal,
    goal_confirm_apply,
    goal_confirm_cancel,
    goal_date,
    goal_type,
)
from app.bot.routers.setup import parse_goal_date
from app.bot.states import GoalStates, PlanStates
from app.db.models.chat_message import ChatMessage
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.db.models.weekly_adherence import WeeklyAdherence
from app.db.repositories import journal_repo, plan_repo, profile_repo, session_log_repo
from app.engine.plan_builder import generate_plan
from tests.test_engine.test_plan_builder import make_profile

# ── parse_goal_date (FR-015) ────────────────────────────────────────────────


def test_parse_goal_date_rejects_the_past_and_garbage():
    assert parse_goal_date("nope")[1] == "format"
    assert parse_goal_date("2000-01-01")[1] == "past"
    assert parse_goal_date("aucune") == (None, None)


def test_parse_goal_date_flags_too_soon_and_too_far_but_keeps_the_date():
    soon = (date.today() + timedelta(days=10)).isoformat()
    d, problem = parse_goal_date(soon)
    assert problem == "too_soon" and d is not None

    far = (date.today() + timedelta(days=500)).isoformat()
    d, problem = parse_goal_date(far)
    assert problem == "too_far" and d is not None

    ok = (date.today() + timedelta(days=90)).isoformat()
    assert parse_goal_date(ok)[1] is None


# ── _regenerate (FR-011, FR-012, SC-005) ────────────────────────────────────


class _Msg:
    def __init__(self, text: str | None = None):
        self.text = text
        self.sent = []
        self.markups = []

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

    async def answer(self, *a, **kw):
        self.answered += 1


class _State:
    def __init__(self):
        self._d = {}
        self.state = None

    async def get_data(self):
        return dict(self._d)

    async def update_data(self, **kw):
        self._d.update(kw)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self._d.clear()
        self.state = None  # matches real aiogram FSMContext.clear() semantics


async def _populate(db_session) -> User:
    u = User(telegram_id=1, first_name="Gwen")
    db_session.add(u)
    await db_session.flush()

    schema = generate_plan(make_profile(goal_type="event"))
    plan = await plan_repo.create(
        db_session, u.id, plan_technical=schema.model_dump(mode="json"),
        start_date=schema.weeks[0].start_date, end_date=schema.weeks[0].start_date,
    )
    prof = make_profile(goal_type="event").model_dump(mode="json")
    await profile_repo.create(db_session, u.id, prof)

    for i in range(6):
        db_session.add(SessionLog(
            user_id=u.id, plan_id=plan.id, week_number=1, day_of_week=i % 7,
            logged_date=date.today() - timedelta(days=i), status="done", tss_actual=60.0,
        ))
    for i in range(3):
        db_session.add(WeeklyAdherence(
            user_id=u.id, week_start_date=date.today() - timedelta(days=7 * (i + 1)),
            sessions_done=3, tss_7d=180.0,
        ))
    for i in range(4):
        db_session.add(ChatMessage(user_id=u.id, role="user", content=f"m{i}"))
    await db_session.flush()
    return u


async def test_goal_change_preserves_all_history(db_session):
    u = await _populate(db_session)
    before = {
        "logs": len(await session_log_repo.get_all_for_user(db_session, u.id)),
        "adh": await db_session.scalar(
            select(func.count()).select_from(WeeklyAdherence)
        ),
        "chat": await db_session.scalar(
            select(func.count()).select_from(ChatMessage)
        ),
    }

    msg, state = _Msg(), _State()
    new_date = date.today() + timedelta(days=120)
    await _regenerate(msg, state, db_session, u, "fitness", new_date)

    after = {
        "logs": len(await session_log_repo.get_all_for_user(db_session, u.id)),
        "adh": await db_session.scalar(
            select(func.count()).select_from(WeeklyAdherence)
        ),
        "chat": await db_session.scalar(
            select(func.count()).select_from(ChatMessage)
        ),
    }
    assert before == after  # SC-005 — 100% preserved

    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "fitness"  # goal changed in place
    assert "gardé" in msg.sent[-1] and "change" in msg.sent[-1]  # FR-016 summary


async def test_goal_change_surfaces_stale_calendar_entries(db_session):
    """FR-014 — sessions published under the old plan are flagged, not left silently stale."""
    import uuid
    from datetime import UTC, datetime

    from app.db.models.publication import PublicationApproval, PublishedEntry

    u = await _populate(db_session)
    old_plan = await plan_repo.get_active_plan(db_session, u.id)
    appr = PublicationApproval(
        id=uuid.uuid4(), user_id=u.id, plan_id=old_plan.id, content_hash="x",
        horizon_start=date.today(), horizon_end=date.today() + timedelta(days=13),
        session_count=1, status="approved", requested_at=datetime.now(UTC),
    )
    db_session.add(appr)
    await db_session.flush()
    db_session.add(PublishedEntry(
        id=uuid.uuid4(), user_id=u.id, plan_id=old_plan.id, approval_id=appr.id,
        external_id="banister:x:2026-09-10:endurance-1-2", intervals_event_id="e1",
        session_date=date.today() + timedelta(days=3), week_number=1, day_of_week=2,
        content_hash="h", published_at=datetime.now(UTC),
    ))
    await db_session.flush()

    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))
    assert "calendrier" in msg.sent[-1] and "/publish" in msg.sent[-1]


# ── Mode switch (spec 009 US2) ───────────────────────────────────────────────


async def _make_user_no_plan(db_session, telegram_id: int = 42) -> User:
    from datetime import UTC
    from datetime import datetime as _datetime

    u = User(
        telegram_id=telegram_id, first_name="Gwen",
        onboarding_completed_at=_datetime.now(UTC),
    )
    db_session.add(u)
    await db_session.flush()
    await profile_repo.create(
        db_session, u.id, make_profile(goal_type="event").model_dump(mode="json")
    )
    return u


async def test_goal_no_longer_blocks_without_an_active_plan(db_session, monkeypatch):
    """spec 009 research Decision 2 — /goal is also the entry point from freestyle mode."""
    u = await _make_user_no_plan(db_session)
    msg, state = _Msg(), _State()

    async def _fresh_profile(_client):
        from app.providers.intervals.athlete_profile import map_athlete_profile

        return map_athlete_profile({
            "sportSettings": [{"types": ["Ride"], "ftp": 250, "max_hr": 190}],
            "icu_resting_hr": 55,
            "icu_date_of_birth": "1990-01-01",
        })

    monkeypatch.setattr("app.bot.routers.goal.read_athlete_profile", _fresh_profile)

    await cmd_goal(msg, state, db_session, u)

    assert "Aucun plan actif" not in msg.sent[-1]
    assert state.state is not None
    assert msg.markups[-1] is not None  # the (now five-option) keyboard was shown


async def test_goal_freestyle_option_deactivates_plan_and_withdraws_calendar(
    db_session, monkeypatch
):
    u = await _populate(db_session)
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None

    calls = []

    async def _fake_withdraw(session, client, user, plan_):
        calls.append(plan_.id)
        return (2, 0)

    monkeypatch.setattr(
        "app.services.publication.withdraw_all_publications", _fake_withdraw
    )

    callback, state = _Callback("goal:type:freestyle"), _State()
    await _enter_freestyle_mode(callback, state, db_session, u)

    assert await plan_repo.get_active_plan(db_session, u.id) is None
    assert calls == [plan.id]
    assert "Mode libre" in callback.message.sent[-1]
    assert "2 séance" in callback.message.sent[-1]
    assert callback.answered == 1

    # Nothing else was touched (FR-012).
    logs_after = await session_log_repo.get_all_for_user(db_session, u.id)
    assert len(logs_after) == 6


async def test_freestyle_choice_requires_confirmation_before_changing_the_plan(db_session):
    u = await _populate(db_session)
    plan = await plan_repo.get_active_plan(db_session, u.id)
    callback, state = _Callback("goal:type:freestyle"), _State()

    await goal_type(callback, state, db_session, u)

    assert state.state == GoalStates.CONFIRM_FREESTYLE
    assert await plan_repo.get_active_plan(db_session, u.id) == plan
    assert callback.message.markups[-1] is not None


async def test_failed_calendar_withdrawal_keeps_the_plan_active(db_session, monkeypatch):
    u = await _populate(db_session)

    async def _partial_failure(session, client, user, plan):
        return (1, 1)

    monkeypatch.setattr(
        "app.services.publication.withdraw_all_publications", _partial_failure
    )
    callback, state = _Callback("goal:freestyle:apply"), _State()

    await _enter_freestyle_mode(callback, state, db_session, u)

    assert await plan_repo.get_active_plan(db_session, u.id) is not None
    assert "reste actif" in callback.message.sent[-1]


def test_goal_preview_compares_first_week_and_peak_load():
    old = generate_plan(make_profile(goal_type="event"))
    new = generate_plan(make_profile(goal_type="fitness"))

    preview = _plan_change_preview(old, new)

    assert "Semaine 1" in preview
    assert "Charge au pic" in preview


async def test_goal_freestyle_option_is_idempotent_when_already_freestyle(db_session):
    u = await _make_user_no_plan(db_session)
    callback, state = _Callback("goal:type:freestyle"), _State()

    await _enter_freestyle_mode(callback, state, db_session, u)

    assert "Déjà en mode libre" in callback.message.sent[-1]
    assert callback.answered == 1


async def test_goal_type_dispatches_freestyle_without_asking_for_a_date(db_session):
    u = await _make_user_no_plan(db_session)
    callback, state = _Callback("goal:type:freestyle"), _State()
    await state.set_state("GoalStates.GOAL")  # arbitrary — goal_type doesn't branch on it

    await goal_type(callback, state, db_session, u)

    assert state.state is None  # cleared, never advanced to GoalStates.DATE
    assert "libre" in callback.message.sent[-1].lower()


async def test_regenerate_from_freestyle_omits_the_old_plan_diff(db_session):
    """spec 009 — entering goal mode from freestyle has no old plan to diff against;
    'ce qui est gardé' still appears, 'ce qui change' does not."""
    u = await _make_user_no_plan(db_session)
    assert await plan_repo.get_active_plan(db_session, u.id) is None

    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))

    summary = msg.sent[-1]
    assert "gardé" in summary
    assert "change" not in summary
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None


async def test_regenerate_from_freestyle_withdraws_pending_freestyle_publications(
    db_session, monkeypatch
):
    """spec 010 FR-011 — leaving freestyle mode withdraws still-future freestyle
    publications, symmetric with spec 009's plan-entry withdrawal in the other direction."""
    from app.db.repositories import freestyle_publication_repo

    u = await _make_user_no_plan(db_session)
    await freestyle_publication_repo.create(
        db_session, user_id=u.id,
        external_id="banister:freestyle:2026-09-20:endurance-55556666",
        intervals_event_id="e1", session_date=date(2026, 9, 20),
        workout_type="endurance", content_hash="h1",
    )

    class _NoopClient:
        async def delete_event(self, event_id):
            pass

    monkeypatch.setattr("app.bot.routers.goal._client", lambda: _NoopClient())

    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))

    assert "mode libre retirée" in msg.sent[-1]
    active = await freestyle_publication_repo.get_active_for_user(db_session, u.id)
    assert active == []


# ── Confirmation gating (revu) — plan actif → confirmer avant d'écraser ──────


_FAR_ENOUGH_DATE = (date.today() + timedelta(days=120)).isoformat()


async def test_goal_date_shows_confirmation_when_a_plan_is_active(db_session):
    u = await _populate(db_session)
    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(goal="fitness")

    await goal_date(msg, state, db_session, u)

    assert state.state == GoalStates.CONFIRM_REGEN
    assert "Confirmer" in msg.sent[-1]
    # Rien n'a encore été écrit — l'ancien plan et l'ancien objectif restent en place.
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "event"


async def test_goal_uses_the_fresh_source_thresholds_for_its_preview(db_session):
    u = await _populate(db_session)
    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(
        goal="fitness",
        _fresh_source_profile={
            "ftp": 310,
            "max_hr": 195,
            "resting_hr": 50,
            "age": 36,
            "coaching_mode": "power",
        },
    )

    await goal_date(msg, state, db_session, u)

    pending = state._d["_pending_profile"]
    assert pending["equipment"]["ftp"] == 310
    assert pending["equipment"]["ftp_source"] == "source"


async def test_goal_date_from_freestyle_applies_immediately_without_confirmation(db_session):
    """spec 009 : venir du mode libre = rien à perdre — comportement inchangé."""
    u = await _make_user_no_plan(db_session)
    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(goal="fitness")

    await goal_date(msg, state, db_session, u)

    assert state.state != GoalStates.CONFIRM_REGEN
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "fitness"


async def test_goal_confirm_cancel_keeps_the_old_plan_untouched(db_session):
    u = await _populate(db_session)
    old_plan = await plan_repo.get_active_plan(db_session, u.id)
    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(goal="fitness")
    await goal_date(msg, state, db_session, u)

    callback = _Callback("goal:confirm:cancel")
    await goal_confirm_cancel(callback, state, db_session, u)

    assert "inchangé" in callback.message.sent[-1].lower()
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan.id == old_plan.id
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "event"
    assert state.state == PlanStates.ACTIVE


async def test_goal_confirm_apply_applies_the_pending_plan(db_session):
    u = await _populate(db_session)
    old_plan = await plan_repo.get_active_plan(db_session, u.id)
    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(goal="fitness")
    await goal_date(msg, state, db_session, u)
    assert state.state == GoalStates.CONFIRM_REGEN

    callback = _Callback("goal:confirm:apply")
    await goal_confirm_apply(callback, state, db_session, u)

    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
    assert plan.id != old_plan.id
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "fitness"
    summary = callback.message.sent[-1]
    assert "gardé" in summary and "change" in summary


async def test_goal_confirm_apply_preserves_history_like_direct_regenerate(db_session):
    """Même garantie SC-005 que test_goal_change_preserves_all_history, via le chemin
    de confirmation cette fois."""
    u = await _populate(db_session)
    before = len(await session_log_repo.get_all_for_user(db_session, u.id))

    msg, state = _Msg(text=_FAR_ENOUGH_DATE), _State()
    await state.update_data(goal="fitness")
    await goal_date(msg, state, db_session, u)
    callback = _Callback("goal:confirm:apply")
    await goal_confirm_apply(callback, state, db_session, u)

    after = len(await session_log_repo.get_all_for_user(db_session, u.id))
    assert before == after


# ── Journal daté (Enduragent parity review, 2026-09-20) ─────────────────────


async def test_goal_change_journals_a_deterministic_goal_change_entry(db_session):
    """_apply_new_plan journals category=goal_change (source=deterministic, text
    templated from already-computed variables) when an old plan existed to diff
    against — distinct from the freestyle_toggle case below."""
    u = await _populate(db_session)
    await _regenerate(_Msg(), _State(), db_session, u, "fitness", None)

    rows = await journal_repo.query(db_session, u.id, date.today(), date.today())
    assert len(rows) == 1
    assert rows[0].category == "goal_change"
    assert rows[0].source == "deterministic"
    assert "fitness" in rows[0].text


async def test_regenerate_from_freestyle_journals_freestyle_toggle_not_goal_change(db_session):
    """Coming from freestyle mode (no old plan to diff) is a mode switch, not an
    in-mode goal change — must journal freestyle_toggle, never both."""
    u = await _make_user_no_plan(db_session, telegram_id=4301)
    await _regenerate(_Msg(), _State(), db_session, u, "fitness", None)

    rows = await journal_repo.query(db_session, u.id, date.today(), date.today())
    assert len(rows) == 1
    assert rows[0].category == "freestyle_toggle"
    assert rows[0].source == "deterministic"


async def test_entering_freestyle_mode_journals_a_freestyle_toggle_entry(db_session, monkeypatch):
    u = await _populate(db_session)

    async def _fake_withdraw(session, client, user, plan_):
        return (0, 0)

    monkeypatch.setattr("app.services.publication.withdraw_all_publications", _fake_withdraw)

    callback, state = _Callback("goal:type:freestyle"), _State()
    await _enter_freestyle_mode(callback, state, db_session, u)

    rows = await journal_repo.query(db_session, u.id, date.today(), date.today())
    assert len(rows) == 1
    assert rows[0].category == "freestyle_toggle"
    assert rows[0].source == "deterministic"


async def test_entering_freestyle_mode_when_already_freestyle_journals_nothing(db_session):
    """Idempotent path (no plan to deactivate) — must not fabricate an event."""
    u = await _make_user_no_plan(db_session, telegram_id=4302)
    callback, state = _Callback("goal:type:freestyle"), _State()
    await _enter_freestyle_mode(callback, state, db_session, u)

    rows = await journal_repo.query(db_session, u.id, date.today(), date.today())
    assert rows == []


async def test_regenerate_from_freestyle_writes_freestyle_toggle_not_goal_change(db_session):
    """Coming from freestyle mode is a mode-switch event, not a plain goal change —
    exactly one journal entry per click, never both categories for the same action."""
    u = await _make_user_no_plan(db_session)
    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))

    rows = await journal_repo.query(db_session, u.id, date.today(), date.today())
    assert [r.category for r in rows] == ["freestyle_toggle"]
