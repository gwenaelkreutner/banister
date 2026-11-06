"""Injury status and reminder settings: what the athlete tells the coach about their situation."""

from __future__ import annotations

from datetime import date, timedelta

from app.core.localization import t
from app.db import repositories as repo
from app.db.models.user import User


def compute_zone_restrictions(severity: str) -> dict:
    """Retourne les restrictions de zones selon la sévérité."""
    if severity == "severe":
        return {"Z3": "Z1", "Z4": "Z1", "Z5": "Z1", "Z6": "Z1"}
    elif severity == "moderate":
        return {"Z4": "Z2", "Z5": "Z2", "Z6": "Z2"}
    else:  # mild
        return {"Z5": "Z3", "Z6": "Z3"}


async def update_injury_status(args: dict, user: User, session, plan) -> dict:
    location = args.get("location", "other")
    severity = args.get("severity", "mild")
    recovery_days = args.get("estimated_recovery_days", 7)

    zone_restrictions = compute_zone_restrictions(severity)
    injury_data = {
        "is_injured": True,
        "location": location,
        "severity": severity,
        "zone_restrictions": zone_restrictions,
        "start_date": str(date.today()),
        "estimated_recovery_date": str(date.today() + timedelta(days=recovery_days)),
    }
    await repo.profile_repo.update_injury_status(session, user.id, injury_data)

    # Journal daté (Enduragent parity review, 2026-09-20) — texte templaté depuis les
    # arguments déjà contraints par les enum JSON-Schema de l'outil (location, severity),
    # jamais de prose libre du LLM (source="deterministic").
    from app.db.repositories import journal_repo
    from app.llm.tools import _body_location_label, _severity_label

    await journal_repo.create(
        session,
        user_id=user.id,
        entry_date=date.today(),
        category="injury",
        source="deterministic",
        text=t(
            "llm.injury_journal_entry",
            location=_body_location_label(location),
            severity=_severity_label(severity),
            days=recovery_days,
        ),
    )

    adapted_weeks = []
    if plan and plan.start_date:
        from app.engine.plan_modifier import adapt_plan_for_injury

        adapted_weeks = adapt_plan_for_injury(plan, injury_data).get("modified_weeks", [])

    return {
        "success": True,
        "injury_recorded": injury_data,
        "zone_restrictions": zone_restrictions,
        "adapted_weeks": adapted_weeks,
        "message": t(
            "llm.tools.injury_recorded",
            location=location,
            severity=severity,
            days=min(recovery_days, 21),
        ),
    }


def reminder_settings(user: User) -> dict:
    return {
        "enabled": user.reminders_enabled,
        "hour": user.reminder_hour,
        "minute": user.reminder_minute,
    }


def update_reminders(
    user: User, *, enabled: bool | None, hour: int | None, minute: int | None
) -> dict:
    if enabled is not None:
        user.reminders_enabled = enabled
    if hour is not None:
        user.reminder_hour = hour
        user.reminder_minute = minute or 0
        # Same as the bot: a new time may still fall later today.
        user.reminder_last_sent_at = None
    return reminder_settings(user)
