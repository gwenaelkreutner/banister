"""Coaching mode (spec 009) — derived, never stored.

`training_plans.is_active` already persists across restarts (`get_active_plan` reads
`status == "active"`), so "which mode is the athlete in" needs no new column and no new
FSM state: an active plan means goal mode, its absence means freestyle mode.
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo

CoachingMode = Literal["goal", "freestyle"]


def mode_from_plan(plan) -> CoachingMode:
    """The single source of truth `get_coaching_mode()` wraps — exposed separately so a
    caller that already loaded the plan (e.g. `app/llm/chat.py`) doesn't need a second
    query just to ask the same question of data it already has."""
    return "goal" if plan is not None else "freestyle"


async def get_coaching_mode(session: AsyncSession, user_id) -> CoachingMode:
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    return mode_from_plan(plan)
