"""Power profile: how the power curve moved, and what the athlete can sustain.

Computed on demand and not stored: several calls to the data source. Shared by `/forme` and the
agent API. `None` when unavailable, never shown as if the data existed.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.core.localization import t
from app.engine.power_curve import (
    PowerCurveDelta,
    SustainabilityProfile,
    compute_power_curve_delta,
    compute_sustainability_profile,
)
from app.engine.schemas import AthleteProfileSchema

logger = logging.getLogger(__name__)

_DELTA_ANCHOR_LABELS = ("5s", "60s", "300s", "1200s", "3600s")
_SUSTAINABILITY_KEY_ANCHORS = (("1200s", "20min"), ("3600s", "60min"))


async def fetch_power_profile(profile: AthleteProfileSchema | None, client) -> str | None:
    """Best-effort, calculé à la demande pour /forme, pas persisté (CLAUDE.md § Power-
    curve delta) — 4 appels réseau intervals.icu (power-curves ×3, wellness du jour pour
    W'). `None` si indisponible (endpoint en échec, pas assez de données, pas de FTP
    déclaré) — jamais affiché comme si la donnée existait."""
    if profile is None or not profile.equipment.ftp:
        return None
    try:
        today = date.today()
        w1_start = (today - timedelta(days=27)).isoformat()
        w1_end = today.isoformat()
        w2_end = (today - timedelta(days=28)).isoformat()
        w2_start = (today - timedelta(days=55)).isoformat()
        cur_id = f"r.{w1_start}.{w1_end}"
        prev_id = f"r.{w2_start}.{w2_end}"

        power_response = await client.get_power_curves(
            curve_type="power",
            windows=[(w1_start, w1_end), (w2_start, w2_end)],
            activity_type="Ride",
        )
        delta = compute_power_curve_delta(
            power_response, current_window_id=cur_id, previous_window_id=prev_id
        )

        sus_start = (today - timedelta(days=41)).isoformat()
        sus_end = today.isoformat()
        sus_id = f"r.{sus_start}.{sus_end}"
        ride_response = await client.get_power_curves(
            curve_type="power", windows=[(sus_start, sus_end)], activity_type="Ride"
        )
        vride_response = await client.get_power_curves(
            curve_type="power", windows=[(sus_start, sus_end)], activity_type="VirtualRide"
        )

        # W' (joules) — pas encore stocké localement, lu depuis le wellness du jour
        # (sportInfo[].wPrime, même chemin que Section11 — voir CLAUDE.md).
        w_prime = None
        wellness_today = await client.list_wellness(oldest=sus_end, newest=sus_end)
        if wellness_today:
            cycling_info = next(
                (s for s in (wellness_today[0].get("sportInfo") or []) if s.get("type") == "Ride"),
                None,
            )
            if cycling_info:
                w_prime = cycling_info.get("wPrime")

        sustainability = compute_sustainability_profile(
            [ride_response, vride_response],
            window_id=sus_id,
            ftp=float(profile.equipment.ftp),
            w_prime=w_prime,
            weight_kg=profile.weight_kg,
        )
        return format_power_profile(delta, sustainability)
    except Exception:
        logger.warning("Impossible de calculer le profil de puissance pour /forme")
        return None


def format_power_profile(
    delta: PowerCurveDelta, sustainability: SustainabilityProfile
) -> str | None:
    blocks: list[str] = []

    if delta.rotation_index is not None:
        parts = []
        for label in _DELTA_ANCHOR_LABELS:
            anchor = delta.anchors.get(label)
            if anchor and anchor.pct_change is not None:
                sign = "+" if anchor.pct_change >= 0 else ""
                parts.append(f"{label} {sign}{anchor.pct_change:.0f}%")
        if parts:
            bias = (
                t("forme.power_profile_bias_sprint")
                if delta.rotation_index > 0
                else t("forme.power_profile_bias_endurance")
            )
            blocks.append(
                t("forme.power_profile_header")
                + "\n"
                + " | ".join(parts)
                + "\n"
                + t(
                    "forme.power_profile_rotation",
                    bias=bias,
                    rotation=f"{delta.rotation_index:+.1f}",
                )
            )

    if sustainability.note is None:
        sus_parts = []
        for key, label in _SUSTAINABILITY_KEY_ANCHORS:
            anchor = sustainability.anchors.get(key)
            if anchor and anchor.actual_watts is not None:
                div = (
                    t("forme.sustainability_divergence", pct=f"{anchor.model_divergence_pct:+.0f}")
                    if anchor.model_divergence_pct is not None
                    else ""
                )
                sus_parts.append(f"{label} : {anchor.actual_watts:.0f}W{div}")
        if sus_parts:
            blocks.append(t("forme.sustainability_header") + "\n" + " | ".join(sus_parts))

    return "\n\n".join(blocks) if blocks else None
