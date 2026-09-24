"""The nutrition confirmation is a SEPARATE Telegram message, not merged into the
coach's reply (user decision, 2026-09-21) — `run_chat()` returns `meal_log` as its own
tuple element, and `handle_chat_message` sends it as a second `<pre>`-wrapped message via
`_send_meal_log`, only when a nutrition tool actually ran this turn.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.bot.routers.chat import handle_chat_message
from app.bot.states import PlanStates
from app.db.models.user import User


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
        return _EditableMessage(self, len(self.sent) - 1)


class _EditableMessage:
    def __init__(self, owner, index):
        self.owner = owner
        self.index = index

    async def edit_text(self, text):
        self.owner.sent[self.index] = (text, None)


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


async def test_meal_log_sent_as_separate_message_after_the_normal_reply(
    db_session, monkeypatch
):
    user = await _make_user(db_session, 9001)
    meal_log_trace = "✅ Enregistré — déjeuner (850 cal)\n🧾 Total du 21/09 : 850 cal"

    async def _fake_run_chat(**kwargs):
        return (
            "Bon repas de midi !",
            "chat",
            "log_meal",
            None,
            {"prompt_tokens": 10, "completion_tokens": 5},
            meal_log_trace,
        )

    monkeypatch.setattr("app.llm.chat.run_chat", _fake_run_chat)

    message = _Message("j'ai mangé du porc au riz")
    state = _State(initial=PlanStates.ACTIVE)

    await handle_chat_message(message, state, db_session, user)

    assert len(message.sent) == 2
    main_text, main_markup = message.sent[0]
    assert "Bon repas de midi" in main_text
    assert main_markup is None

    log_text, log_markup = message.sent[1]
    assert log_text == meal_log_trace
    assert log_markup is None


async def test_no_meal_log_means_no_second_message(db_session, monkeypatch):
    user = await _make_user(db_session, 9002)

    async def _fake_run_chat(**kwargs):
        return (
            "Voici tes séances de la semaine.",
            "chat",
            "get_upcoming_sessions",
            None,
            {"prompt_tokens": 10, "completion_tokens": 5},
            None,
        )

    monkeypatch.setattr("app.llm.chat.run_chat", _fake_run_chat)

    message = _Message("montre-moi mon planning")
    state = _State(initial=PlanStates.ACTIVE)

    await handle_chat_message(message, state, db_session, user)

    assert len(message.sent) == 1


async def test_tool_trace_precedes_coach_reply(db_session, monkeypatch):
    user = await _make_user(db_session, 9003)
    message = _Message("montre-moi mon planning")

    async def _fake_run_chat(**kwargs):
        await kwargs["on_tool_event"]("get_upcoming_sessions", "started")
        assert message.sent == [("Outils utilisés :\n⏳ get_upcoming_sessions", None)]
        await kwargs["on_tool_event"]("get_upcoming_sessions", "finished")
        return (
            "Voici ton planning.", "chat", "get_upcoming_sessions", None,
            {"prompt_tokens": 10, "completion_tokens": 5}, None,
        )

    monkeypatch.setattr("app.llm.chat.run_chat", _fake_run_chat)
    await handle_chat_message(message, _State(initial=PlanStates.ACTIVE), db_session, user)

    assert message.sent == [
        ("Outils utilisés :\n✅ get_upcoming_sessions", None),
        ("Voici ton planning.", None),
    ]
