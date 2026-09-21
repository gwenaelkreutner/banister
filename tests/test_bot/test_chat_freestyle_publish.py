"""spec 010 — the freestyle-publish confirmation button.

Foundational (Decision 2): a freestyle suggestion's confirm button must not push the FSM
into PENDING_MODIFICATION the way a plan-modification proposal does, since negotiating a
different session (US2) has to keep working as ordinary chat.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.bot.routers.chat import cb_publish_freestyle, handle_chat_message
from app.bot.states import PlanStates
from app.db.models.user import User
from app.db.repositories import freestyle_publication_repo, profile_repo
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.services.publication import FreestylePublishOutcome


class _Bot:
    async def send_chat_action(self, chat_id, action):
        pass


class _Chat:
    id = 123


class _Message:
    def __init__(self, text: str):
        self.text = text
        self.bot = _Bot()
        self.chat = _Chat()
        self.reply_to_message = None
        self.sent: list[tuple[str, object]] = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))


class _CallbackMessage:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _Callback:
    def __init__(self, data: str):
        self.data = data
        self.message = _CallbackMessage()
        self.answered: list[tuple] = []

    async def answer(self, *a, **kw):
        self.answered.append((a, kw))


class _State:
    def __init__(self, initial=None):
        self._state = initial
        self._d = {}

    async def get_state(self):
        return self._state

    async def set_state(self, s):
        self._state = s

    async def get_data(self):
        return dict(self._d)

    async def update_data(self, **kw):
        self._d.update(kw)

    async def clear(self):
        self._d.clear()
        self._state = None


async def _make_user(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id, first_name="Test",
        onboarding_completed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


def _freestyle_result(suggestion_id: str = "aaaa1111") -> dict:
    return {
        "available": True,
        "type": "freestyle_publish",
        "id": suggestion_id,
        "workout_type": "endurance",
        "duration_minutes": 90,
        "target_tss": 62,
        "zone_code": "Z2",
        "reasoning_summary": "TSB -6 -> endurance.",
        "steps": [{"kind": "steady", "duration_minutes": 90, "zone_code": "Z2"}],
    }


async def test_freestyle_suggestion_attaches_button_and_keeps_state_active(
    db_session, monkeypatch
):
    user = await _make_user(db_session, 8001)
    result = _freestyle_result()

    async def _fake_run_chat(**kwargs):
        return (
            "90 min en Z2, tu me dis si ça te va.",
            "freestyle_suggestion",
            "get_freestyle_session_suggestion",
            result,
            {"prompt_tokens": 10, "completion_tokens": 5},
        )

    monkeypatch.setattr("app.llm.chat.run_chat", _fake_run_chat)

    message = _Message("je veux rouler aujourd'hui")
    state = _State(initial=PlanStates.ACTIVE)

    await handle_chat_message(message, state, db_session, user)

    assert len(message.sent) == 1
    text, markup = message.sent[0]
    assert "90 min" in text
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == f"freestyle:publish:{result['id']}"

    # Foundational Decision 2 — state must stay ACTIVE, never PENDING_MODIFICATION.
    assert await state.get_state() == PlanStates.ACTIVE

    data = await state.get_data()
    assert data["pending_freestyle_id"] == result["id"]
    assert data["pending_freestyle_suggestion"] == result


async def test_plan_modification_proposal_still_blocks_as_before(db_session, monkeypatch):
    """Regression guard: extending pending_proposal for freestyle must not change the
    existing plan-modification behaviour."""
    user = await _make_user(db_session, 8002)
    modification_result = {"week_number": 3, "tss_after": 210}

    async def _fake_run_chat(**kwargs):
        return (
            "Je réduis la semaine 3.",
            "plan_modification",
            "propose_plan_modification",
            modification_result,
            {"prompt_tokens": 10, "completion_tokens": 5},
        )

    monkeypatch.setattr("app.llm.chat.run_chat", _fake_run_chat)

    message = _Message("allège ma semaine")
    state = _State(initial=PlanStates.ACTIVE)

    await handle_chat_message(message, state, db_session, user)

    assert await state.get_state() == PlanStates.PENDING_MODIFICATION
    data = await state.get_data()
    assert data["pending_modification"] == modification_result
    assert "pending_freestyle_id" not in data


# ── cb_publish_freestyle (US1/US2) ───────────────────────────────────────────────


async def _make_profile(session, user_id) -> None:
    schema = AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness"),
        availability=AvailabilityProfile(hours_per_week=6, preferred_days=["tuesday"]),
        level="intermediate",
        structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
        physio=PhysioProfile(age=35, hr_max=185, hr_rest=50),
        coaching_mode="power",
        health_constraints=False,
    )
    await profile_repo.create(session, user_id, schema.model_dump(mode="json"))


async def test_publish_button_writes_calendar_entry_and_clears_pending(
    db_session, monkeypatch
):
    user = await _make_user(db_session, 8003)
    await _make_profile(db_session, user.id)
    result = _freestyle_result("bbbb2222")
    state = _State(initial=PlanStates.ACTIVE)
    await state.update_data(
        pending_freestyle_id=result["id"], pending_freestyle_suggestion=result
    )

    async def _fake_publish(*a, **kw):
        return FreestylePublishOutcome(
            status="created", external_id="banister:freestyle:2026-09-20:endurance-x",
            intervals_event_id="e999", content_hash="deadbeef",
        )

    monkeypatch.setattr("app.services.publication.publish_freestyle_session", _fake_publish)

    callback = _Callback(f"freestyle:publish:{result['id']}")
    await cb_publish_freestyle(callback, state, db_session, user)

    assert callback.message.edits and "publiée" in callback.message.edits[-1]
    data = await state.get_data()
    assert data["pending_freestyle_id"] is None
    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert len(active) == 1
    assert active[0].intervals_event_id == "e999"


async def test_publish_button_stale_id_rejects_without_writing(db_session, monkeypatch):
    """US1 Acceptance Scenario 2 / US2 Acceptance Scenario 1 — tapping an id that is no
    longer the current one never publishes, and never even reaches publish_freestyle_session."""
    user = await _make_user(db_session, 8004)
    await _make_profile(db_session, user.id)
    current = _freestyle_result("cccc3333")
    state = _State(initial=PlanStates.ACTIVE)
    await state.update_data(
        pending_freestyle_id=current["id"], pending_freestyle_suggestion=current
    )

    calls = []

    async def _fake_publish(*a, **kw):
        calls.append(1)

    monkeypatch.setattr("app.services.publication.publish_freestyle_session", _fake_publish)

    stale_callback = _Callback("freestyle:publish:aaaa1111")  # an earlier, superseded id
    await cb_publish_freestyle(stale_callback, state, db_session, user)

    assert calls == []
    assert stale_callback.answered
    assert stale_callback.message.edits == []
    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert active == []


async def test_publish_button_with_nothing_pending_rejects(db_session, monkeypatch):
    user = await _make_user(db_session, 8005)
    await _make_profile(db_session, user.id)
    state = _State(initial=PlanStates.ACTIVE)  # nothing pending at all

    callback = _Callback("freestyle:publish:zzzz9999")
    await cb_publish_freestyle(callback, state, db_session, user)

    assert callback.answered
    assert callback.message.edits == []


async def test_publish_failure_keeps_pending_id_for_retry(db_session, monkeypatch):
    """US1 Acceptance Scenario 3 / FR-008 — a calendar-write failure never claims
    success, and leaves pending_freestyle_id intact so retapping is a real retry."""
    user = await _make_user(db_session, 8006)
    await _make_profile(db_session, user.id)
    result = _freestyle_result("dddd4444")
    state = _State(initial=PlanStates.ACTIVE)
    await state.update_data(
        pending_freestyle_id=result["id"], pending_freestyle_suggestion=result
    )

    async def _fake_publish_failure(*a, **kw):
        return FreestylePublishOutcome(status="failed", detail="RuntimeError: simulated outage")

    monkeypatch.setattr(
        "app.services.publication.publish_freestyle_session", _fake_publish_failure
    )

    callback = _Callback(f"freestyle:publish:{result['id']}")
    await cb_publish_freestyle(callback, state, db_session, user)

    assert callback.message.edits and "n'a pas abouti" in callback.message.edits[-1]
    data = await state.get_data()
    assert data["pending_freestyle_id"] == result["id"]  # untouched — a retry is valid
    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert active == []


async def test_publishing_a_second_suggestion_never_touches_the_first(db_session, monkeypatch):
    """US2 Acceptance Scenario 2 — publishing B after A must not withdraw or alter A."""
    user = await _make_user(db_session, 8007)
    await _make_profile(db_session, user.id)
    suggestion_a = _freestyle_result("eeee5555")
    suggestion_b = _freestyle_result("ffff6666")
    state = _State(initial=PlanStates.ACTIVE)

    outcomes = [
        FreestylePublishOutcome(
            status="created", external_id="banister:freestyle:2026-09-20:endurance-a",
            intervals_event_id="event-a", content_hash="hash-a",
        ),
        FreestylePublishOutcome(
            status="created", external_id="banister:freestyle:2026-09-20:endurance-b",
            intervals_event_id="event-b", content_hash="hash-b",
        ),
    ]

    async def _fake_publish_seq(*a, **kw):
        return outcomes.pop(0)

    monkeypatch.setattr("app.services.publication.publish_freestyle_session", _fake_publish_seq)

    await state.update_data(
        pending_freestyle_id=suggestion_a["id"], pending_freestyle_suggestion=suggestion_a
    )
    await cb_publish_freestyle(
        _Callback(f"freestyle:publish:{suggestion_a['id']}"), state, db_session, user
    )

    await state.update_data(
        pending_freestyle_id=suggestion_b["id"], pending_freestyle_suggestion=suggestion_b
    )
    await cb_publish_freestyle(
        _Callback(f"freestyle:publish:{suggestion_b['id']}"), state, db_session, user
    )

    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert {e.intervals_event_id for e in active} == {"event-a", "event-b"}
