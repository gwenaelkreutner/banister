"""First-run setup: confirmation screen + profile building (spec 007 US1, FR-005/FR-010).

The FSM wiring is exercised live (quickstart Scenario 1); these tests pin the pure
helpers — the screen renderer, the FSM-flatten, and `_build_profile` reading confirmed
source values.
"""
from __future__ import annotations

from datetime import date

from app.bot.routers.setup import (
    _build_profile,
    _built_from_recap,
    _read_profile_to_fsm,
    _render_confirm_screen,
)
from app.providers.intervals.athlete_profile import map_athlete_profile

_ATHLETE = {
    "id": "iX",
    "sex": "M",
    "locale": "fr",
    "icu_weight": 67.0,
    "icu_resting_hr": 65,
    "icu_date_of_birth": "1990-01-01",
    "sportSettings": [
        {"types": ["Ride", "VirtualRide"], "ftp": 290, "lthr": 182, "max_hr": 202},
    ],
}


def test_confirm_screen_shows_every_value_with_its_origin():
    rp = map_athlete_profile(_ATHLETE)
    screen = _render_confirm_screen(rp)
    assert "290" in screen and "Réglages sport" in screen        # FTP + origin
    assert "202" in screen and "182" in screen                    # HR figures
    assert "intervals.icu" in screen
    assert "mesurée" in screen                                    # the RHR caveat (FR-005)
    assert "puissance" in screen                                  # coaching mode stated


def test_no_cycling_ftp_screen_says_hr_mode():
    payload = {**_ATHLETE, "sportSettings": [{"types": ["Ride"], "lthr": 180, "max_hr": 195}]}
    rp = map_athlete_profile(payload)
    screen = _render_confirm_screen(rp)
    assert "fréquence cardiaque" in screen
    assert "absent" in screen  # FTP row shows absent, not a default


def test_build_profile_marks_source_values_and_never_asks_age():
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "event", "target_date": "2026-12-01", "hours_per_week": 8,
                "health_constraints": False})

    profile = _build_profile(fsm)
    assert profile.equipment.ftp == 290
    assert profile.equipment.ftp_source == "source"
    assert profile.physio.hr_max == 202
    assert profile.physio.hr_max_source == "source"
    assert profile.physio.hr_rest == 65
    assert profile.physio.hr_rest_source == "source"
    # age came from DOB, not a question
    dob = date(1998, 6, 29)
    today = date.today()
    assert profile.physio.age == today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )
    assert profile.coaching_mode == "power"


def test_build_profile_keeps_the_source_value_when_the_athlete_corrected_it():
    """FR-007 — a correction is kept at the source's current value, not the wanted one."""
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({
        "goal": "event", "hours_per_week": 8, "health_constraints": False,
        "corrections_deferred": {"ftp": {"wanted": 305.0, "current": 290}},
    })
    profile = _build_profile(fsm)
    assert profile.equipment.ftp == 290  # NOT 305 (FR-007)

    recap = _built_from_recap(profile, fsm, seeded=False)
    assert "305" in recap and "290" in recap and "intervals.icu" in recap


def test_missing_source_ftp_falls_back_to_hr_mode_no_silent_default():
    payload = {**_ATHLETE, "sportSettings": [{"types": ["Ride"], "lthr": 180, "max_hr": 195}]}
    rp = map_athlete_profile(payload)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "fitness", "hours_per_week": 6, "health_constraints": False})
    profile = _build_profile(fsm)
    assert profile.coaching_mode == "hr"
    assert profile.equipment.power_meter is False


def test_seeded_fitness_is_disclosed_in_the_recap():
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "fitness", "hours_per_week": 6, "health_constraints": False})
    profile = _build_profile(fsm)
    recap = _built_from_recap(profile, fsm, seeded=True)
    assert "estimation prudente" in recap  # FR-010


# ── US2: the correction flow (FR-006a, FR-007, SC-004) ───────────────────────


class _FakeState:
    def __init__(self, data: dict):
        self._data = dict(data)
        self.state = None

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, s):
        self.state = s


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.sent: list[str] = []

    async def answer(self, text, **kw):
        self.sent.append(text)


async def test_correcting_ftp_defers_to_the_source_and_shows_from_to():
    from app.bot.routers.setup import correct_value

    state = _FakeState({"read_ftp": 290, "_correcting": "ftp", "corrections_deferred": {}})
    msg = _FakeMessage("305")
    await correct_value(msg, state)

    data = await state.get_data()
    assert data["corrections_deferred"]["ftp"] == {"wanted": 305.0, "current": 290}
    assert data["_correcting"] is None
    body = msg.sent[-1]
    assert "305" in body and "290" in body           # from → to (FR-006a)
    assert "intervals.icu" in body and "Réglages" in body  # sent to the source (FR-007)


async def test_correction_with_no_field_selected_is_a_noop():
    from app.bot.routers.setup import correct_value

    state = _FakeState({"read_ftp": 290})
    msg = _FakeMessage("305")
    await correct_value(msg, state)
    assert (await state.get_data()).get("corrections_deferred") in (None, {})


# ── AVAILABLE_DAYS (found 2026-09-18: preferred_days was hardcoded, never asked) ──


class _FakeCallbackMessage:
    def __init__(self):
        self.texts: list[str] = []
        self.markups: list = []

    async def edit_text(self, text, **kw):
        self.texts.append(text)
        self.markups.append(kw.get("reply_markup"))

    async def edit_reply_markup(self, reply_markup=None, **kw):
        self.markups.append(reply_markup)


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeCallbackMessage()
        self.answered: list[dict] = []

    async def answer(self, text: str | None = None, **kw):
        self.answered.append({"text": text, **kw})


def _markup_data(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


async def test_setup_volume_preselects_the_legacy_default_days():
    from app.bot.routers.setup import _DEFAULT_AVAILABLE_DAYS, setup_volume
    from app.bot.states import SetupStates

    state = _FakeState({})
    cb = _FakeCallback("setup:vol:7")
    await setup_volume(cb, state)

    data = await state.get_data()
    assert data["available_days"] == _DEFAULT_AVAILABLE_DAYS
    assert data["hours_per_week"] == 7.0
    assert state.state == SetupStates.AVAILABLE_DAYS


async def test_days_toggle_adds_and_removes():
    from app.bot.routers.setup import setup_days_toggle

    state = _FakeState({"available_days": ["tuesday", "thursday"]})
    await setup_days_toggle(_FakeCallback("setup:day:monday"), state)
    assert set((await state.get_data())["available_days"]) == {"tuesday", "thursday", "monday"}

    await setup_days_toggle(_FakeCallback("setup:day:tuesday"), state)
    assert set((await state.get_data())["available_days"]) == {"thursday", "monday"}


async def test_days_confirm_blocks_below_minimum():
    from app.bot.routers.setup import setup_days_confirm

    state = _FakeState({"available_days": ["monday"]})
    cb = _FakeCallback("setup:days:confirm")
    await setup_days_confirm(cb, state)

    assert state.state is None  # never advanced past AVAILABLE_DAYS
    assert cb.answered[-1]["show_alert"] is True


async def test_days_confirm_advances_to_constraints_when_enough_days():
    from app.bot.routers.setup import setup_days_confirm
    from app.bot.states import SetupStates

    state = _FakeState({"available_days": ["monday", "wednesday"]})
    cb = _FakeCallback("setup:days:confirm")
    await setup_days_confirm(cb, state)

    assert state.state == SetupStates.CONSTRAINTS
    assert "contrainte santé" in cb.message.texts[-1]


def test_build_profile_uses_the_athletes_chosen_days():
    from app.bot.routers.setup import _build_profile

    fsm = {
        "goal": "fitness", "hours_per_week": 6, "health_constraints": False,
        "available_days": ["monday", "wednesday", "friday"],
    }
    profile = _build_profile(fsm)
    assert profile.availability.preferred_days == ["monday", "wednesday", "friday"]


def test_build_profile_falls_back_to_default_when_days_missing():
    """Safety net only — the live flow always sets available_days via AVAILABLE_DAYS."""
    from app.bot.routers.setup import _DEFAULT_AVAILABLE_DAYS, _build_profile

    fsm = {"goal": "fitness", "hours_per_week": 6, "health_constraints": False}
    profile = _build_profile(fsm)
    assert profile.availability.preferred_days == _DEFAULT_AVAILABLE_DAYS


def test_recap_lists_the_chosen_days_in_french():
    from app.bot.routers.setup import _build_profile, _built_from_recap

    fsm = {
        "goal": "fitness", "hours_per_week": 6, "health_constraints": False,
        "available_days": ["sunday", "monday"],
    }
    profile = _build_profile(fsm)
    recap = _built_from_recap(profile, fsm, seeded=False)
    assert "Lundi" in recap and "Dimanche" in recap
    assert recap.index("Lundi") < recap.index("Dimanche")  # chronological, not insertion order
