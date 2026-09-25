from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.plan import overview_keyboard, week_navigation_keyboard
from app.bot.text_format import to_telegram_html
from app.config import settings
from app.core.localization import t
from app.db import repositories as repo
from app.db.models.user import User
from app.engine.schemas import TrainingPlanSchema, WeekPlan
from app.engine.session_render import render_session_description

router = Router()

PHASE_EMOJI = {
    "base": "🔵",
    "build": "🟠",
    "peak": "🔴",
    "taper": "🟣",
}


def _format_duration_fr(minutes: int) -> str:
    """Format compact lisible pour amateur: 68 -> 1h8, 50 -> 50m."""
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h{mins}"


@router.message(Command("plan"))
async def cmd_plan(message: Message, state: FSMContext, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer(
            t("plan.no_plan_yet")
        )
        return

    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if not db_plan:
        await message.answer(
            t("plan.no_active_plan_start")
        )
        return

    plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)
    current_week = _get_current_week_number(plan, db_plan.start_date)
    await _send_week(message, plan, current_week, edit=False)


@router.message(Command("week"))
async def cmd_week(message: Message, state: FSMContext, session: AsyncSession, user: User):
    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if not db_plan:
        await message.answer(
            t("plan.no_active_plan_week")
        )
        return

    plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)

    parts = message.text.strip().split()
    if len(parts) < 2:
        week_num = _get_current_week_number(plan, db_plan.start_date)
    else:
        try:
            week_num = int(parts[1])
        except ValueError:
            await message.answer(
                t("plan.invalid_week_number")
            )
            return

    if not (1 <= week_num <= plan.weeks_count):
        await message.answer(
            t("plan.week_out_of_range", count=plan.weeks_count)
        )
        return

    await _send_week(message, plan, week_num, edit=False)

    week = next((w for w in plan.weeks if w.week_number == week_num), None)
    if week:
        from app.llm.narrator import generate_week_narrative

        narrative = await generate_week_narrative(week, plan.weeks_count)
        await message.answer(narrative)


@router.callback_query(F.data.startswith("plan:week:"))
async def cb_week(callback: CallbackQuery, session: AsyncSession, user: User):
    week_num = int(callback.data.split(":")[-1])
    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if not db_plan:
        await callback.answer(t("plan.no_active_plan"), show_alert=True)
        return

    plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)
    await callback.answer()
    await _send_week(callback.message, plan, week_num, edit=True)


@router.callback_query(F.data == "plan:current")
async def cb_current_week(callback: CallbackQuery, session: AsyncSession, user: User):
    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if not db_plan:
        await callback.answer(t("plan.no_active_plan"), show_alert=True)
        return

    plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)
    current_week = _get_current_week_number(plan, db_plan.start_date)
    await callback.answer()
    await _send_week(callback.message, plan, current_week, edit=True)


@router.callback_query(F.data == "plan:overview")
async def cb_overview(callback: CallbackQuery, session: AsyncSession, user: User):
    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if not db_plan:
        await callback.answer(t("plan.no_active_plan"), show_alert=True)
        return

    plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)
    narrative = db_plan.plan_narrative or {}
    intro = narrative.get("intro", "")

    overview = _build_overview(plan, intro)
    await callback.answer()
    await callback.message.edit_text(overview, parse_mode="HTML", reply_markup=overview_keyboard())


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _send_week(message, plan: TrainingPlanSchema, week_num: int, edit: bool = False):
    week = next((w for w in plan.weeks if w.week_number == week_num), None)
    if not week:
        text = t("plan.week_not_found", week=week_num)
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    text = _format_week(week, plan.weeks_count, plan.coaching_mode)
    keyboard = week_navigation_keyboard(week_num, plan.weeks_count)

    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


def _format_week(week: WeekPlan, weeks_count: int, coaching_mode: str = "hr") -> str:
    language = settings.app_language
    phase = t(f"plan.phase.{week.phase}")
    phase_emoji = PHASE_EMOJI.get(week.phase, "📌")
    recovery_tag = t("plan.recovery_tag") if week.is_recovery_week else ""
    date_str = f"  ·  {week.start_date.strftime('%d/%m')}" if week.start_date else ""

    lines = [
        t("plan.week_heading", week=week.week_number, count=weeks_count, date=date_str),
        (f"{phase_emoji} <b>{to_telegram_html(phase)}</b>{recovery_tag}  ·  "
         f"{t('plan.target_tss')} <b>{week.total_tss_target:.0f}</b>"),
        "",
    ]

    for s in week.sessions:
        day = t(f"day.short.{s.day_of_week}")
        workout = t(f"plan.workout.{s.workout_type}")
        desc = to_telegram_html(
            render_session_description(s, coaching_mode=coaching_mode, language=language)
        )
        lines.append(
            f"<b>{day}</b>  ·  {workout} <b>{s.zone_code}</b>  ·  "
            f"⏱️ {_format_duration_fr(s.duration_minutes)}  ·  ~{s.tss_target:.0f} TSS"
        )
        if desc:
            lines.append(f"<i>{desc}</i>")
        lines.append("")

    return "\n".join(lines).rstrip()


def _build_overview(plan: TrainingPlanSchema, llm_intro: str) -> str:
    lines = [
        t("plan.overview_heading", count=plan.weeks_count)
    ]

    if llm_intro:
        lines.append(to_telegram_html(llm_intro))
        lines.append("")

    lines.append(t("plan.phases_heading"))
    seen = set()
    for w in plan.weeks:
        if w.phase in seen:
            continue
        seen.add(w.phase)
        phase_weeks = [x for x in plan.weeks if x.phase == w.phase]
        phase_label = t(f"plan.phase.{w.phase}")
        phase_emoji = PHASE_EMOJI.get(w.phase, "📌")
        avg_tss = sum(x.total_tss_target for x in phase_weeks) / len(phase_weeks)
        start_week = phase_weeks[0].week_number
        end_week = phase_weeks[-1].week_number
        week_range = (
            t("plan.week_range_single", start=start_week)
            if start_week == end_week
            else t("plan.week_range", start=start_week, end=end_week)
        )
        lines.append(
            f"{phase_emoji} <b>{to_telegram_html(phase_label)}</b>  ·  {week_range}  ·  "
            f"~{avg_tss:.0f} {t('plan.tss_per_week')}"
        )

    lines += [
        "",
        t("plan.progression", initial=plan.initial_weekly_tss, peak=plan.peak_weekly_tss),
        t("plan.mode_power" if plan.coaching_mode == "power" else "plan.mode_hr"),
        "",
        t("plan.tss_explanation"),
    ]
    return "\n".join(lines)


def _get_current_week_number(plan: TrainingPlanSchema, start_date: date) -> int:
    today = date.today()
    if today < start_date:
        return 1
    days_elapsed = (today - start_date).days
    week_num = days_elapsed // 7 + 1
    return min(week_num, plan.weeks_count)
