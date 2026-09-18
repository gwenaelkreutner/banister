"""spec 010 US3 — /unpublish offers to withdraw freestyle publications when there is no
active plan, instead of "Aucun plan actif" (symmetric with spec 009's /goal extension).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.bot.routers.publish import cb_withdraw_all_freestyle, cmd_unpublish
from app.db.models.user import User
from app.db.repositories import freestyle_publication_repo


class _Message:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))


class _CallbackMessage:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class _Callback:
    def __init__(self):
        self.message = _CallbackMessage()
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


async def _make_user(session, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id, first_name="Test",
        onboarding_completed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def test_unpublish_with_no_plan_and_nothing_freestyle_says_nothing_to_remove(
    db_session,
):
    user = await _make_user(db_session, 9001)
    message = _Message()

    await cmd_unpublish(message, db_session, user)

    assert "Rien n'est actuellement publié" in message.sent[-1][0]


async def test_unpublish_with_no_plan_offers_freestyle_withdrawal(db_session):
    user = await _make_user(db_session, 9002)
    await freestyle_publication_repo.create(
        db_session, user_id=user.id,
        external_id="banister:freestyle:2026-09-20:endurance-11112222",
        intervals_event_id="e1", session_date=date(2026, 9, 20),
        workout_type="endurance", content_hash="h1",
    )
    message = _Message()

    await cmd_unpublish(message, db_session, user)

    text, markup = message.sent[-1]
    assert "mode libre" in text
    assert markup is not None
    assert markup.inline_keyboard[0][0].callback_data == "pub:withdrawall_freestyle"


async def test_withdraw_all_freestyle_callback_removes_only_freestyle_entries(
    db_session, monkeypatch
):
    user = await _make_user(db_session, 9003)
    await freestyle_publication_repo.create(
        db_session, user_id=user.id,
        external_id="banister:freestyle:2026-09-20:endurance-33334444",
        intervals_event_id="e2", session_date=date(2026, 9, 20),
        workout_type="endurance", content_hash="h2",
    )

    # Only the network client is faked — the real withdraw_freestyle_publications runs,
    # exercising the actual repo path end to end.
    class _NoopClient:
        async def delete_event(self, event_id):
            pass

    monkeypatch.setattr("app.bot.routers.publish._client", lambda: _NoopClient())

    callback = _Callback()
    await cb_withdraw_all_freestyle(callback, db_session, user)

    assert callback.answered == 1
    assert any("retirée" in edit for edit in callback.message.edits)
    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert active == []
