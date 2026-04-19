from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.engine.atl_ctl import FitnessMetrics
from app.strava.analysis_models import AnalyzedSession, RawActivity
from app.strava.webhook import _notify_staged_rpe_request, _notify_unplanned


def _raw(name: str = "Sortie test", duration_s: int = 3600) -> RawActivity:
    return RawActivity(
        activity_id="42",
        sport_type="ride",
        strava_sport_type="Ride",
        name=name,
        start_datetime=datetime(2026, 1, 6, 10, 0),
        duration_s=duration_s,
    )


def _analyzed(session_type_real: str = "intervals", tss: float = 78.0) -> AnalyzedSession:
    return AnalyzedSession(
        session_id="42",
        source="strava",
        sport_type="ride",
        start_datetime=datetime(2026, 1, 6, 10, 0),
        duration_s=3600,
        has_power=True,
        has_heartrate=True,
        has_gps=True,
        tss=tss,
        session_type_real=session_type_real,
        dominant_zone="Z4",
        environment="outdoor",
        intensity_factor=0.92,
        normalized_power=250.0,
        normalized_power_source="strava",
    )


class DummyBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.chat_actions: int = 0

    async def send_message(self, telegram_id, text, parse_mode=None, reply_markup=None,
                           disable_notification=False):
        self.messages.append({
            "telegram_id": telegram_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "disable_notification": disable_notification,
        })

    async def send_chat_action(self, telegram_id, action):
        self.chat_actions += 1


@pytest.mark.asyncio
async def test_notify_staged_sends_three_messages(monkeypatch):
    """La notification stagée envoie exactement 3 messages."""
    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())
    monkeypatch.setattr("app.bot.keyboards.session_log.rpe_emoji_keyboard_strava", lambda _: "kb")
    monkeypatch.setattr("app.strava.webhook.build_message_a", lambda h: "Teaser A")
    monkeypatch.setattr("app.strava.webhook.build_message_b", lambda h, a, pr=None: "Métrique B")

    bot = DummyBot()
    highlight = MagicMock()

    await _notify_staged_rpe_request(
        bot=bot,
        telegram_id=123,
        raw=_raw(),
        analyzed=_analyzed(),
        session_spec=SimpleNamespace(workout_type="intervals", duration_minutes=90, zone_code="Z4"),
        log_id="log-1",
        match_level="exact",
        confidence_score=91,
        day_shift=0,
        fitness_feedback="📊 CTL 55 · ATL 60 · TSB -5",
        fitness_metrics=FitnessMetrics(ctl=55.0, atl=60.0, tsb=-5.0),
        highlight=highlight,
    )

    assert len(bot.messages) == 3

    # Messages A et B sont silencieux
    assert bot.messages[0]["disable_notification"] is True
    assert bot.messages[1]["disable_notification"] is True
    # Message C est la notification principale (pas disable_notification)
    assert bot.messages[2]["disable_notification"] is False or bot.messages[2]["disable_notification"] is None

    # Message C contient le score et le verdict
    verdict = bot.messages[2]["text"]
    assert "91/100" in verdict
    assert "séance respectée" in verdict


@pytest.mark.asyncio
async def test_notify_staged_day_shift_displayed(monkeypatch):
    """Le décalage de jours est affiché dans le message C."""
    monkeypatch.setattr("asyncio.sleep", lambda _: _noop())
    monkeypatch.setattr("app.bot.keyboards.session_log.rpe_emoji_keyboard_strava", lambda _: "kb")
    monkeypatch.setattr("app.strava.webhook.build_message_a", lambda h: "A")
    monkeypatch.setattr("app.strava.webhook.build_message_b", lambda h, a, pr=None: "B")

    bot = DummyBot()

    await _notify_staged_rpe_request(
        bot=bot,
        telegram_id=123,
        raw=_raw(),
        analyzed=_analyzed(session_type_real="endurance", tss=65.0),
        session_spec=SimpleNamespace(workout_type="endurance", duration_minutes=90, zone_code="Z2"),
        log_id="log-2",
        match_level="close",
        confidence_score=72,
        day_shift=1,
        fitness_feedback="feedback",
        fitness_metrics=FitnessMetrics(ctl=50.0, atl=55.0, tsb=-5.0),
        highlight=MagicMock(),
    )

    verdict = bot.messages[2]["text"]
    assert "1 jour" in verdict  # décalage affiché


@pytest.mark.asyncio
async def test_notify_unplanned_uses_raw_fields(monkeypatch):
    """_notify_unplanned utilise RawActivity directement (pas de dict legacy)."""
    bot = DummyBot()
    raw = _raw(name="Sortie random", duration_s=2700)

    await _notify_unplanned(
        bot=bot,
        telegram_id=456,
        raw=raw,
        reason="Aucune séance planifiée trouvée à ±2 jours.",
        tss=45.0,
    )

    assert len(bot.messages) == 1
    text = bot.messages[0]["text"]
    assert "Sortie random" in text
    assert "45 min" in text  # 2700s = 45 min
    assert "45 TSS" in text
    assert "hors plan" in text.lower()


async def _noop():
    """Coroutine vide pour remplacer asyncio.sleep dans les tests."""
