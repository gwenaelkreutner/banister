"""The athlete's gestures on an agent's cards and messages.

Every button carries an action string. This is the only place they take effect, so each thing an
agent can only propose has exactly one way of becoming real. Callers MUST NOT expose `dispatch`
to a model. A refused or failed action raises `ActionError` and leaves the card open.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db import repositories as repo
from app.db.models.user import User
from app.services import plan_management, publication, ride_feedback, session_design
from app.services.plan_management import PlanChangeError
from app.services.session_design import SessionDesignError

# Instructions to the agent, not athlete-facing: the agent replies in the athlete's language.
RIDE_REVIEW_PROMPT = (
    "[Banister] The athlete just finished the ride of {date} and {feeling}. Read it with "
    "banister_ride, then give them your short review: what it shows, what it changes for the "
    "next days. Do not ask them to repeat anything."
)
WEEKLY_RECAP_PROMPT = (
    "[Banister] The weekly recap figures were just sent to the athlete. Read banister_week_recap "
    "and banister_context, then give them your short analysis of the week and what to aim for "
    "next week. Do not repeat the figures one by one."
)


class ActionError(ValueError):
    def __init__(self, message: str, *, status: int = 409):
        super().__init__(message)
        self.status = status


def _result(text: str, *, parse_mode: str | None = None, agent_prompt: str | None = None) -> dict:
    return {
        "text": text,
        "parse_mode": parse_mode,
        "agent_prompt": agent_prompt,
        "handover_hint": t("agent.handover_hint") if agent_prompt else None,
    }


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ActionError("invalid action", status=422) from exc


async def _rpe(session, user, log_id: str, token: str, client_factory) -> dict:
    valid, rpe = ride_feedback.parse_rpe(token)
    if not valid:
        raise ActionError(t("session_log.rpe_out_of_scale"), status=422)
    try:
        log, synced = await ride_feedback.record_rpe(
            session, user, _uuid(log_id), rpe, client_factory()
        )
    except ride_feedback.RideNotFound as exc:
        raise ActionError(t("session_log.log_not_found"), status=404) from exc
    await session.commit()
    text = (
        t("session_log.rpe_noted_value", value=int(rpe))
        if rpe is not None
        else t("session_log.rpe_skipped")
    )
    if not synced:
        text += "\n" + t("session_log.rpe_sync_failed")
    feeling = f"rated it {int(rpe)}/10" if rpe is not None else "gave no rating"
    return _result(
        text,
        parse_mode="HTML",
        agent_prompt=RIDE_REVIEW_PROMPT.format(date=log.logged_date.isoformat(), feeling=feeling),
    )


async def request_plan_publication(session: AsyncSession, user: User) -> dict:
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        raise PlanChangeError(t("publish.no_active_plan_setup"), status=409)
    request = await publication.request_publication(session, user, plan)
    if request.session_count == 0:
        raise PlanChangeError(
            t(
                "publish.no_structured_sessions",
                start=request.approval.horizon_start.strftime("%d/%m"),
                end=request.approval.horizon_end.strftime("%d/%m"),
            ),
            status=409,
        )
    approval_id = request.approval.id
    return {
        "session_count": request.session_count,
        "card": {
            "text": request.text,
            "parse_mode": "HTML",
            "buttons": [
                [
                    {
                        "label": t("agent.card.publish_plan"),
                        "action": f"calendar:approve:{approval_id}",
                    },
                    {
                        "label": t("agent.card.change_cancel"),
                        "action": f"calendar:decline:{approval_id}",
                    },
                ]
            ],
        },
    }


async def request_plan_withdrawal(session: AsyncSession, user: User) -> dict:
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        raise PlanChangeError(t("publish.no_active_plan"), status=409)
    active = await repo.publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    if not active:
        raise PlanChangeError(t("publish.nothing_published"), status=409)
    return {
        "count": len(active),
        "card": {
            "text": t("publish.withdraw_plan_prompt", count=len(active)),
            "parse_mode": "HTML",
            "buttons": [
                [
                    {"label": t("agent.card.withdraw_all"), "action": "calendar:withdraw_all"},
                    {"label": t("agent.card.keep"), "action": "dismiss:publish.nothing_withdrawn"},
                ]
            ],
        },
    }


async def _calendar_approve(session, user, approval_value: str, client_factory) -> dict:
    approval_id = _uuid(approval_value)
    approval = await repo.publication_repo.get_approval(session, approval_id)
    if approval is None or approval.user_id != user.id:
        raise ActionError(t("publish.request_not_found"), status=404)
    if approval.status != "pending":
        raise ActionError(t("publish.request_already_handled"))
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None or plan.id != approval.plan_id:
        raise ActionError(t("publish.plan_changed_retry"))

    await repo.publication_repo.mark_approved(session, approval_id)
    try:
        report = await publication.execute_publication(
            session, client_factory(), user, plan, approval
        )
    except publication.StaleApprovalError as exc:
        await session.commit()
        raise ActionError(t("publish.stale_approval")) from exc
    except publication.PublicationNotAuthorized as exc:
        await session.commit()
        raise ActionError(t("publish.invalid_request")) from exc
    await session.commit()
    return _result(report.text, parse_mode="HTML")


async def _calendar_decline(session, user, approval_value: str) -> dict:
    approval = await repo.publication_repo.get_approval(session, _uuid(approval_value))
    if approval is None or approval.user_id != user.id:
        raise ActionError(t("publish.request_not_found"), status=404)
    if approval.status != "pending":
        raise ActionError(t("publish.request_already_handled"))
    await repo.publication_repo.mark_declined(session, approval.id)
    await session.commit()
    return _result(t("agent.card.calendar_declined"))


async def _calendar_withdraw_all(session, user, client_factory) -> dict:
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        raise ActionError(t("publish.no_active_plan"))
    withdrawn, failed = await publication.withdraw_all_publications(
        session, client_factory(), user, plan
    )
    await session.commit()
    key = "publish.withdraw_partial_failure" if failed else "publish.withdraw_success"
    return _result(t(key, withdrawn=withdrawn, failed=failed), parse_mode="HTML")


async def dispatch(
    session: AsyncSession,
    user: User,
    action: str,
    client_factory: Callable,
    *,
    today: date | None = None,
) -> dict:
    domain, _, rest = action.partition(":")
    verb, _, value = rest.partition(":")
    try:
        if domain == "log" and verb == "rpe":
            log_id, _, token = value.partition(":")
            return await _rpe(session, user, log_id, token, client_factory)
        if domain == "proposal" and verb == "approve":
            result = await session_design.approve_proposal(
                session, user, value, client_factory(), today=today
            )
            return _result(result["text"])
        if domain == "proposal" and verb == "decline":
            return _result((await session_design.decline_proposal(session, user, value))["text"])
        if domain == "publication" and verb == "withdraw":
            result = await session_design.withdraw_publication(
                session, user, _uuid(value), client_factory()
            )
            return _result(result["text"])
        if domain == "change" and verb == "apply":
            text = await plan_management.apply_change(session, user, value, client_factory)
            return _result(text, parse_mode="HTML")
        if domain == "change" and verb == "cancel":
            return _result(await plan_management.cancel_change(session, user, value))
        if domain == "calendar" and verb == "approve":
            return await _calendar_approve(session, user, value, client_factory)
        if domain == "calendar" and verb == "decline":
            return await _calendar_decline(session, user, value)
        if domain == "calendar" and verb == "withdraw_all":
            return await _calendar_withdraw_all(session, user, client_factory)
        if domain == "dismiss" and verb in _DISMISS_TEXTS:
            return _result(t(verb), parse_mode="HTML")
    except (SessionDesignError, PlanChangeError) as exc:
        raise ActionError(str(exc), status=exc.status) from exc
    raise ActionError("unknown action", status=422)


_DISMISS_TEXTS = frozenset({"agent.card.kept", "publish.nothing_withdrawn"})
