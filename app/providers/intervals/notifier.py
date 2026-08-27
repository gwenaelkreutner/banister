"""Points the staged post-activity notification at the poller instead of Strava's
inbound webhook (spec 002 T045-T049, FR-030..FR-034, Plan Phase E — the cutover).

Reuses the RPE capture/reveal machinery already in app/bot/routers/session_log.py
(`cb_rpe_strava`, keyboard `rpe_emoji_keyboard_strava`) unchanged: that handler works
entirely on `SessionLog` rows, not raw Strava objects, so nothing about it is actually
Strava-specific — only its naming is historical. Renaming it, and the structural-debt
cleanup inside it (T063-T065), is Phase 7's job once app/strava/ is deleted outright;
touching it now would be risk on already-working code for no benefit this phase.
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.repositories import sync_state_repo
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.mapper import map_activity_to_analyzed_session
from app.services.activity_feedback import ActivityFeedbackContext, assemble_activity_feedback
from app.strava.highlight import build_message_a, build_message_b, build_message_c_session_card

logger = logging.getLogger(__name__)


def _parse_activity_date(payload: dict):
    from datetime import datetime

    start = payload.get("start_date_local") or payload["start_date"]
    return datetime.fromisoformat(start.replace("Z", "+00:00")).date()


def _build_rpe_prompt(session_type_real: str | None, tsb: float) -> str:
    """Duplicated from app/strava/webhook.py rather than imported — that module is
    deleted whole in Phase 7."""
    if tsb <= -25:
        return "Comment tu te sens physiquement là ?"
    if tsb >= 10:
        return "Tu as senti la forme aujourd'hui ?"

    pools: dict[str, list[str]] = {
        "intervals": [
            "Tes jambes ont tenu la cadence ?",
            "Fatigue musculaire ou cardio en fin de séance ?",
            "Le dernier intervalle, tu l'as senti comment ?",
        ],
        "long_ride": [
            "Jambes encore fraîches à l'arrivée, ou en mode survie ? 😅",
            "Comment tu te sens là, maintenant ?",
        ],
        "recovery": [
            "Vraiment récupération, ou ça tirait un peu ?",
            "Tu as pu rester facile du début à la fin ?",
        ],
        "endurance": [
            "Ça coulait tout seul ou il fallait pousser ?",
            "Comment c'était ?",
        ],
        "race": [
            "Tout ce que tu avais, tu l'as mis là-dedans ?",
            "Comment tu te sens après l'effort ?",
        ],
        "tempo": [
            "Les jambes ont répondu jusqu'au bout ?",
            "Tu as maintenu l'intensité voulue ?",
        ],
    }
    pool = pools.get(
        session_type_real or "", ["Comment c'était ?", "Ton ressenti ?", "Physiquement, ça allait ?"]
    )
    return random.choice(pool)


async def process_detected_activity(
    session: AsyncSession,
    user: User,
    client: IntervalsClient,
    activity_summary: dict,
    *,
    announce: bool,
) -> ActivityFeedbackContext:
    """No aiogram here — mapping + context assembly only, same testability guarantee as
    assemble_activity_feedback itself. `announce=True` fetches the activity's full detail
    with `icu_intervals` (needed for intervals_consistency_index, research R5) — a real
    extra API call, made only for activities about to be shown to the athlete, matching
    the quota assumption research R4/R5 documented. `announce=False` (ingest-only
    backlog, FR-011) reuses the summary payload already in hand — everything except
    intervals_consistency_index is already there (research R9a), and that one metric
    isn't worth a whole extra request for an activity nobody is about to see analyzed.
    """
    activity_id = str(activity_summary["id"])
    payload = (
        await client.get_activity(activity_id, with_intervals=True) if announce else activity_summary
    )

    initial_analyzed = map_activity_to_analyzed_session(payload)
    activity_date = _parse_activity_date(payload)

    def reanalyze(planned_zone: str, planned_target_time_in_zone_s: int):
        return map_activity_to_analyzed_session(
            payload,
            planned_zone=planned_zone,
            planned_target_time_in_zone_s=planned_target_time_in_zone_s,
        )

    return await assemble_activity_feedback(
        session,
        user,
        initial_analyzed,
        activity_date,
        source="intervals_icu",
        source_activity_id=activity_id,
        reanalyze_with_plan=reanalyze,
    )


async def send_staged_notification(bot, telegram_id: int, context: ActivityFeedbackContext) -> bool:
    """The three-message staged exchange (FR-030), unchanged in ordering, notable-aspect
    selection (highlight.py, untouched) and restraint (only Message C vibrates). Returns
    whether delivery actually succeeded — the caller (`notify_detected_activity`) must
    not mark the activity reported unless this is True (FR-012).
    """
    assert context.log is not None and context.highlight is not None
    fm = context.fitness_metrics
    tsb = fm.tsb if fm else 0.0

    try:
        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(1.2)
        await bot.send_message(
            telegram_id,
            build_message_a(context.highlight),
            parse_mode="HTML",
            disable_notification=True,
        )

        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(0.8)
        await bot.send_message(
            telegram_id,
            build_message_b(context.highlight, context.analyzed, pr=None),
            parse_mode="HTML",
            disable_notification=True,
        )

        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(1.0)

        from app.bot.keyboards.session_log import rpe_emoji_keyboard_strava

        match_score = context.match_result.score if context.match_result else None
        candidate = context.match_result.candidate if context.match_result else None
        rpe_prompt = _build_rpe_prompt(context.analyzed.session_type_real, tsb)
        card = build_message_c_session_card(
            analyzed=context.analyzed,
            session_spec=candidate.session_spec if candidate else None,
            rpe_prompt=rpe_prompt,
            match_level=match_score.match_level if match_score else "exact",
            confidence_score=match_score.confidence_score if match_score else None,
            day_shift=candidate.day_shift if candidate else 0,
        )
        await bot.send_message(
            telegram_id,
            card,
            parse_mode="HTML",
            reply_markup=rpe_emoji_keyboard_strava(str(context.log.id)),
        )
        return True
    except Exception:
        logger.exception("Erreur lors de l'envoi de la notification stagée (intervals.icu)")
        return False


async def _notify_simple(
    bot, telegram_id: int, title: str, context: ActivityFeedbackContext
) -> bool:
    """Shared shape for the bonus/unplanned notifications — never framed as an error
    (FR-034), matching webhook.py's existing _notify_bonus_activity/_notify_unplanned."""
    analyzed = context.analyzed
    elapsed_min = int((analyzed.moving_time_s or analyzed.duration_s) / 60)
    tss_text = f" · ~{analyzed.tss:.0f} TSS" if analyzed.tss is not None else ""
    reason_text = f"\n\n{context.reason}" if context.reason else ""
    details_text = (
        "\n" + "\n".join(f"• {d}" for d in context.reason_details)
        if context.reason_details
        else ""
    )
    fitness_text = f"\n\n{context.fitness_feedback}" if context.fitness_feedback else ""

    try:
        await bot.send_message(
            telegram_id,
            f"{title}\n\n"
            f"{elapsed_min} min{tss_text}"
            f"{reason_text}{details_text}"
            f"{fitness_text}",
            parse_mode="HTML",
        )
        return True
    except Exception:
        logger.exception("Erreur lors de l'envoi de la notification (intervals.icu, %s)", title)
        return False


async def notify_detected_activity(
    session: AsyncSession,
    user: User,
    bot,
    client: IntervalsClient,
    activity_summary: dict,
    *,
    announce: bool,
) -> None:
    """Processes one detected activity end to end and marks it reported once — and only
    once — whatever was supposed to happen actually happened (FR-012): the staged
    notification actually sent for an announced match, or the (silent) ingestion itself
    for everything else. A failure here leaves the activity unreported, so the next poll
    tick retries it rather than losing it (FR-009).
    """
    activity_id = str(activity_summary["id"])
    context = await process_detected_activity(
        session, user, client, activity_summary, announce=announce
    )

    if context.outcome == "no_plan":
        # No active plan to match against at all — same silent skip as the Strava path
        # (nothing to ingest without a plan_id, SessionLog.plan_id is NOT NULL). Left
        # unreported deliberately: once a plan exists, a later poll within the 7-day
        # window should still pick this activity up.
        logger.debug(
            "No active plan for user %s — activity %s left unprocessed.", user.id, activity_id
        )
        return

    delivered = True
    if announce:
        if context.outcome == "matched":
            delivered = await send_staged_notification(bot, user.telegram_id, context)
        elif context.outcome == "bonus":
            delivered = await _notify_simple(
                bot, user.telegram_id, "🔄 <b>Sortie bonus enregistrée</b>", context
            )
        else:  # "unplanned"
            delivered = await _notify_simple(
                bot, user.telegram_id, "🚴 <b>Activité hors plan détectée</b>", context
            )

    await session.commit()

    if delivered:
        await sync_state_repo.mark_reported(
            session, user.id, activity_id, reported_at=_utcnow()
        )
        await session.commit()


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)
