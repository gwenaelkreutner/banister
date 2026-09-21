"""`/forme` — profil de puissance (power-curve delta + sustainability_profile,
2026-09-21). Pas de test d'intégration complet de `cmd_forme` (aucun pattern existant
pour ça dans ce fichier) — couvre `_fetch_power_profile()` (câblage réseau best-effort,
même convention `_client()` que `app/bot/routers/review.py`) et `_format_power_profile()`
(formatage pur) isolément.
"""
from __future__ import annotations

from app.bot.routers.forme import _fetch_power_profile, _format_power_profile
from app.engine.power_curve import (
    AnchorDelta,
    PowerCurveDelta,
    SustainabilityAnchor,
    SustainabilityProfile,
)
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)


def _profile(*, ftp: int | None = 250, weight_kg: float | None = 70.0) -> AthleteProfileSchema:
    return AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness"),
        availability=AvailabilityProfile(hours_per_week=6, preferred_days=["tuesday"]),
        level="intermediate",
        structured_plan_history=False,
        equipment=EquipmentProfile(power_meter=True, ftp=ftp),
        physio=PhysioProfile(age=35, hr_max=185, hr_rest=50),
        coaching_mode="power",
        health_constraints=False,
        weight_kg=weight_kg,
    )


class _FakePowerCurvesClient:
    def __init__(self, power_response, wellness_response=None):
        self._power_response = power_response
        self._wellness_response = wellness_response or []
        self.power_calls = []
        self.wellness_calls = 0

    async def get_power_curves(self, *, curve_type, windows, activity_type=None):
        self.power_calls.append((curve_type, tuple(windows), activity_type))
        return self._power_response

    async def list_wellness(self, *, oldest, newest):
        self.wellness_calls += 1
        return self._wellness_response


# ── _fetch_power_profile ──────────────────────────────────────────────────────


async def test_no_profile_returns_none():
    assert await _fetch_power_profile(None) is None


async def test_no_ftp_returns_none():
    assert await _fetch_power_profile(_profile(ftp=None)) is None


async def test_network_error_returns_none(monkeypatch):
    class _RaisingClient:
        async def get_power_curves(self, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.bot.routers.forme._client", lambda: _RaisingClient())
    assert await _fetch_power_profile(_profile()) is None


async def test_fetches_three_power_curve_calls_and_one_wellness_call(monkeypatch):
    fake = _FakePowerCurvesClient(power_response={"list": [], "activities": []})
    monkeypatch.setattr("app.bot.routers.forme._client", lambda: fake)

    await _fetch_power_profile(_profile())

    # delta (1 appel, 2 fenêtres) + sustainability Ride + sustainability VirtualRide
    assert len(fake.power_calls) == 3
    assert fake.power_calls[0][0] == "power"
    assert fake.wellness_calls == 1


async def test_empty_power_curves_response_yields_none_text(monkeypatch):
    fake = _FakePowerCurvesClient(power_response={"list": [], "activities": []})
    monkeypatch.setattr("app.bot.routers.forme._client", lambda: fake)

    result = await _fetch_power_profile(_profile())

    assert result is None  # delta.rotation_index=None + sustainability.note != None


# ── _format_power_profile ─────────────────────────────────────────────────────


def test_format_returns_none_when_both_blocks_empty():
    delta = PowerCurveDelta(anchors={}, rotation_index=None, note="pas de données")
    sustainability = SustainabilityProfile(
        anchors={}, coverage_ratio=0.0, ftp_used=250.0, w_prime_used=None, note="pas assez",
    )
    assert _format_power_profile(delta, sustainability) is None


def test_format_includes_rotation_bias_and_anchors():
    delta = PowerCurveDelta(
        anchors={
            "5s": AnchorDelta(current_watts=1100, previous_watts=1000, pct_change=10.0),
            "60s": AnchorDelta(current_watts=500, previous_watts=400, pct_change=25.0),
            "300s": AnchorDelta(current_watts=300, previous_watts=300, pct_change=0.0),
            "1200s": AnchorDelta(current_watts=250, previous_watts=250, pct_change=0.0),
            "3600s": AnchorDelta(current_watts=200, previous_watts=200, pct_change=0.0),
        },
        rotation_index=17.5,
        note=None,
    )
    sustainability = SustainabilityProfile(
        anchors={}, coverage_ratio=0.0, ftp_used=250.0, w_prime_used=None, note="pas assez",
    )
    text = _format_power_profile(delta, sustainability)
    assert text is not None
    assert "sprint" in text
    assert "+10%" in text and "+25%" in text
    assert "Soutenabilité" not in text  # bloc omis, sustainability.note != None


def test_format_includes_sustainability_with_model_divergence():
    delta = PowerCurveDelta(anchors={}, rotation_index=None, note="pas de données")
    sustainability = SustainabilityProfile(
        anchors={
            "1200s": SustainabilityAnchor(
                duration_s=1200, actual_watts=270.0, actual_wpkg=None,
                coggan_watts=233, cp_model_watts=267, model_divergence_pct=1.1,
            ),
            "3600s": SustainabilityAnchor(
                duration_s=3600, actual_watts=None, actual_wpkg=None,
                coggan_watts=None, cp_model_watts=None, model_divergence_pct=None,
            ),
        },
        coverage_ratio=0.5, ftp_used=250.0, w_prime_used=20000.0, note=None,
    )
    text = _format_power_profile(delta, sustainability)
    assert text is not None
    assert "Soutenabilité" in text
    assert "20min" in text and "270W" in text
    assert "60min" not in text  # pas de watts observés pour cet ancrage
