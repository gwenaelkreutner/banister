"""
Définitions des outils LLM (format OpenAI-compatible) pour le chat agentique.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.localization import t
from app.engine.atl_ctl import FitnessMetrics
from app.engine.freestyle_selector import VALID_WORKOUT_TYPES
from app.engine.rpe import rpe_emoji as _rpe_emoji_for
from app.engine.rpe import rpe_label
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.engine.zones import compute_hr_zones
from app.llm.prompt_fence import sanitize_untrusted_text, wrap_untrusted_block

# ── Schémas des outils (format OpenAI tool_use) ──────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_fitness_history",
            "description": t("llm.tools.get_fitness_history.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 7, "maximum": 365},
                    "granularity": {
                        "type": "string", "enum": ["daily", "weekly"],
                        "description": t("llm.tools.get_fitness_history.granularity_description"),
                    },
                },
                "required": ["days", "granularity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_trend",
            "description": t("llm.tools.get_training_trend.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 7, "maximum": 90},
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_detail",
            "description": t("llm.tools.get_session_detail.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": t("llm.tools.get_session_detail.date_description"),
                    },
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wellness_history",
            "description": t("llm.tools.get_wellness_history.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 7, "maximum": 90},
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "hrv", "resting_hr", "sleep_score", "fatigue", "stress",
                                "motivation", "weight_kg",
                            ],
                        },
                        "maxItems": 4,
                    },
                    "granularity": {"type": "string", "enum": ["daily", "weekly"]},
                },
                "required": ["days", "metrics", "granularity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_sessions",
            "description": t("llm.tools.get_upcoming_sessions.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": t("llm.tools.get_upcoming_sessions.days_description"),
                        "minimum": 1,
                        "maximum": 42,
                    },
                    "start_offset": {
                        "type": "integer",
                        "description": t("llm.tools.get_upcoming_sessions.start_offset_description"),
                        "minimum": -7,
                        "maximum": 56,
                    },
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_injury_status",
            "description": t("llm.tools.update_injury_status.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "enum": ["knee", "back", "shoulder", "hip", "ankle", "other"],
                        "description": t("llm.tools.update_injury_status.location_description"),
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["mild", "moderate", "severe"],
                        "description": t("llm.tools.update_injury_status.severity_description"),
                    },
                    "estimated_recovery_days": {
                        "type": "integer",
                        "description": t("llm.tools.update_injury_status.recovery_days_description"),
                        "minimum": 1,
                        "maximum": 90,
                    },
                },
                "required": ["location", "severity", "estimated_recovery_days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_plan_modification",
            "description": t("llm.tools.propose_plan_modification.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": ["fatigue", "injury", "event", "preference", "illness"],
                        "description": t("llm.tools.propose_plan_modification.reason_description"),
                    },
                    "modification_type": {
                        "type": "string",
                        "enum": ["reduce_intensity", "reduce_volume", "skip_session", "swap_to_recovery"],
                        "description": t("llm.tools.propose_plan_modification.modification_type_description"),
                    },
                    "week_offset": {
                        "type": "integer",
                        "description": t("llm.tools.propose_plan_modification.week_offset_description"),
                        "minimum": 0,
                        "maximum": 2,
                    },
                },
                "required": ["reason", "modification_type", "week_offset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_session_adjustment",
            "description": t("llm.tools.propose_session_adjustment.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "constraint_type": {
                        "type": "string",
                        "enum": ["meeting", "weather_bad", "tired_today", "personal"],
                    },
                    "day_offset": {
                        "type": "integer",
                        "description": t("llm.tools.propose_session_adjustment.day_offset_description"),
                        "minimum": 0,
                        "maximum": 6,
                    },
                    "action": {
                        "type": "string",
                        "enum": ["skip", "shift", "reduce_50", "indoor"],
                    },
                },
                "required": ["constraint_type", "day_offset", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_coach_memory",
            "description": t("llm.tools.update_coach_memory.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add_note", "update_athlete_notes"],
                        "description": t("llm.tools.update_coach_memory.action_description"),
                    },
                    "category": {
                        "type": "string",
                        "enum": ["fatigue", "motivation", "physique", "event", "preference"],
                        "description": t("llm.tools.update_coach_memory.category_description"),
                    },
                    "note": {
                        "type": "string",
                        "description": t("llm.tools.update_coach_memory.note_description"),
                    },
                    "key": {
                        "type": "string",
                        "description": t("llm.tools.update_coach_memory.key_description"),
                    },
                    "value": {
                        "type": "string",
                        "description": t("llm.tools.update_coach_memory.value_description"),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_freestyle_session_suggestion",
            "description": t("llm.tools.get_freestyle_session_suggestion.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "requested_workout_type": {
                        "type": "string",
                        "enum": sorted(VALID_WORKOUT_TYPES),
                        "description": t(
                            "llm.tools.get_freestyle_session_suggestion.requested_workout_type_description"
                        ),
                    },
                    "max_duration_minutes": {
                        "type": "integer",
                        "minimum": 15,
                        "maximum": 300,
                        "description": t(
                            "llm.tools.get_freestyle_session_suggestion.max_duration_description"
                        ),
                    },
                    "requested_duration_minutes": {
                        "type": "integer",
                        "minimum": 15,
                        "maximum": 300,
                        "description": t(
                            "llm.tools.get_freestyle_session_suggestion.requested_duration_description"
                        ),
                    },
                    "style_preference": {
                        "type": "string",
                        "description": t(
                            "llm.tools.get_freestyle_session_suggestion.style_preference_description"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_meal",
            "description": t("llm.tools.log_meal.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {
                        "type": "string",
                        "enum": ["meal", "day_recap"],
                        "description": t("llm.tools.log_meal.entry_type_description"),
                    },
                    "meal_slot": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack", "other"],
                        "description": t("llm.tools.log_meal.meal_slot_description"),
                    },
                    "estimated_calories": {
                        "type": "integer",
                        "description": t("llm.tools.log_meal.estimated_calories_description"),
                        "minimum": 1,
                        "maximum": 8000,
                    },
                    "days_ago": {
                        "type": "integer",
                        "description": t("llm.tools.log_meal.days_ago_description"),
                        "minimum": 0,
                        "maximum": 2,
                    },
                },
                "required": ["entry_type", "estimated_calories"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_last_meal_entry",
            "description": t("llm.tools.undo_last_meal_entry.description"),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_calorie_history",
            "description": t("llm.tools.get_calorie_history.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": t("llm.tools.get_calorie_history.days_description"),
                        "minimum": 1,
                        "maximum": 30,
                    },
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": t("llm.tools.memory_query.description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_date": {
                        "type": "string",
                        "description": t("llm.tools.memory_query.from_date_description"),
                    },
                    "to_date": {
                        "type": "string",
                        "description": t("llm.tools.memory_query.to_date_description"),
                    },
                    "keyword": {
                        "type": "string",
                        "description": t("llm.tools.memory_query.keyword_description"),
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "fatigue", "motivation", "physique", "event", "preference",
                            "goal_change", "injury", "freestyle_toggle",
                        ],
                        "description": t("llm.tools.memory_query.category_description"),
                    },
                },
                "required": ["from_date", "to_date"],
            },
        },
    },
]

# Tools that only make sense with an active plan — offering them in freestyle mode
# would let the model attempt an action that can never succeed there (spec 009 research
# Decision 6). `get_freestyle_session_suggestion` is the symmetric case: it never
# appears in goal mode.
_GOAL_ONLY_TOOLS = frozenset(
    {"get_upcoming_sessions", "propose_plan_modification", "propose_session_adjustment"}
)
_FREESTYLE_ONLY_TOOLS = frozenset({"get_freestyle_session_suggestion"})

# These calls never write and do not depend on another tool result from the same wave.
# `run_chat` executes them with independent read sessions before `asyncio.gather()`.
PARALLEL_READ_TOOLS = frozenset({
    "get_upcoming_sessions",
    "get_fitness_history",
    "get_training_trend",
    "get_session_detail",
    "get_wellness_history",
})


def tools_for_mode(coaching_mode: str) -> list[dict]:
    """Filters `TOOL_DEFINITIONS` by coaching mode (`"goal"` or `"freestyle"`) before it
    is handed to the model — never let it call a tool that has nothing to act on."""
    excluded = _FREESTYLE_ONLY_TOOLS if coaching_mode == "goal" else _GOAL_ONLY_TOOLS
    return [t for t in TOOL_DEFINITIONS if t["function"]["name"] not in excluded]


# ── Construction du system prompt ─────────────────────────────────────────────

_DAY_KEYS = [
    "day.full.monday", "day.full.tuesday", "day.full.wednesday", "day.full.thursday",
    "day.full.friday", "day.full.saturday", "day.full.sunday",
]
_MONTH_KEYS = [f"llm.month.{i}" for i in range(12)]
_WORKOUT_KEYS = {
    "long_ride": "session.workout.long_ride", "intervals": "session.workout.intervals",
    "endurance": "session.workout.endurance", "recovery": "session.workout.recovery",
}
_LEVEL_KEYS = {
    "beginner": "llm.level.beginner", "intermediate": "llm.level.intermediate",
    "advanced": "llm.level.advanced", "expert": "llm.level.expert",
}
_GOAL_KEYS = {
    "event": "llm.tools_goal.event", "fitness": "llm.tools_goal.fitness",
    "performance": "llm.tools_goal.performance", "other": "llm.tools_goal.other",
}
_SEVERITY_KEYS = {
    "mild": "llm.severity.mild", "moderate": "llm.severity.moderate", "severe": "llm.severity.severe",
}
_LOCATION_KEYS = {
    "knee": "llm.body_location.knee", "back": "llm.body_location.back",
    "shoulder": "llm.body_location.shoulder", "hip": "llm.body_location.hip",
    "ankle": "llm.body_location.ankle", "other": "llm.body_location.other",
}


def _day_name(dow: int) -> str:
    return t(_DAY_KEYS[dow])


def _day_short(dow: int) -> str:
    return _day_name(dow)[:3]


def _workout_label(workout_type: str) -> str:
    key = _WORKOUT_KEYS.get(workout_type)
    return t(key) if key else workout_type


def _level_label(level: str) -> str:
    key = _LEVEL_KEYS.get(level)
    return t(key) if key else level


def _goal_label(goal: str) -> str:
    key = _GOAL_KEYS.get(goal)
    return t(key) if key else goal


def _severity_label(severity: str) -> str:
    key = _SEVERITY_KEYS.get(severity)
    return t(key) if key else severity


def _body_location_label(location: str) -> str:
    key = _LOCATION_KEYS.get(location)
    return t(key) if key else location


def _temporal_reference_rules(now: datetime) -> list[str]:
    """State one authoritative temporal reference for the whole model turn."""
    current_date = now.date()
    long_date = (
        f"{_day_name(current_date.weekday()).lower()} {current_date.day} "
        f"{t(_MONTH_KEYS[current_date.month - 1])} {current_date.year}"
    )
    return [
        t("llm.temporal_reference.header"),
        t(
            "llm.temporal_reference.now",
            long_date=long_date, time=now.strftime("%H:%M"), iso=current_date.isoformat(),
        ),
        t("llm.temporal_reference.rule1"),
        t("llm.temporal_reference.rule2"),
    ]


def _format_week_pairs(pairs: list, week_num: int, phase: str, today: date) -> list[str]:
    """Formate les paires plan/réalisé pour le system prompt."""
    lines = [t("llm.week_pairs.header", week=week_num, phase=phase)]

    for pair in pairs:
        dow_short = _day_short(pair.day_of_week)
        date_str = pair.planned_date.strftime("%d/%m")

        if pair.session_spec and pair.session_log:
            # ── Séance planifiée + réalisée ────────────────────────────────
            spec = pair.session_spec
            log = pair.session_log
            dur_pl = spec.duration_minutes
            dur_ac = getattr(log, "duration_minutes_actual", None)
            tss_pl = round(spec.tss_target)
            tss_ac = getattr(log, "tss_actual", None)
            stype = getattr(log, "session_type_real", None) or ""
            dzone = getattr(log, "dominant_zone", None)
            rpe = getattr(log, "rpe", None)
            elev = getattr(log, "elevation_gain_m", None)
            group = (getattr(log, "athlete_count", 1) or 1) > 1

            # Date réelle du log (peut différer de la date planifiée si décalage)
            actual_date = getattr(log, "logged_date", pair.planned_date)
            if actual_date != pair.planned_date:
                actual_dow = _day_short(actual_date.weekday())
                date_label = f"{actual_dow} {actual_date.strftime('%d/%m')} (plan {date_str})"
            else:
                date_label = f"{dow_short} {date_str}"

            dur_str = (
                f"{dur_pl}→{dur_ac}min" if dur_ac
                else t("llm.week_pairs.duration_planned_suffix", minutes=dur_pl)
            )
            if tss_ac and tss_pl:
                diff_pct = (tss_ac / tss_pl - 1) * 100
                tss_str = f"TSS {tss_pl}→{tss_ac:.0f} ({diff_pct:+.0f}%)"
            else:
                tss_str = t("llm.week_pairs.tss_planned_suffix", tss=tss_pl)

            extras = []
            if stype and stype != "unknown":
                extras.append(stype)
            if dzone:
                zone_flag = " ⚠️" if dzone != spec.zone_code else ""
                extras.append(f"zone {dzone}{zone_flag}")
            if elev and elev > 500:
                extras.append(f"{elev:.0f}m D+")
            if group:
                extras.append(t("llm.week_pairs.group_tag"))
            if rpe is not None:
                extras.append(f"RPE {_rpe_emoji_for(rpe)} {rpe:.0f}/10")

            wtype = _workout_label(spec.workout_type)
            extras_str = f" · {' · '.join(extras)}" if extras else ""
            lines.append(f"  ✅ {date_label} · {wtype} {spec.zone_code} · {dur_str} · {tss_str}{extras_str}")

        elif pair.session_spec and not pair.session_log:
            # ── Séance planifiée non réalisée ──────────────────────────────
            spec = pair.session_spec
            wtype = _workout_label(spec.workout_type)
            suffix = (
                t("llm.week_pairs.not_done_suffix") if pair.planned_date <= today
                else t("llm.week_pairs.upcoming_suffix")
            )
            mark = "❌" if pair.planned_date <= today else "📅"
            lines.append(
                f"  {mark} {dow_short} {date_str} · {wtype} {spec.zone_code} — "
                f"{spec.duration_minutes}min (TSS {round(spec.tss_target)}){suffix}"
            )

        elif not pair.session_spec and pair.session_log:
            # ── Activité bonus non planifiée ───────────────────────────────
            log = pair.session_log
            dur_ac = getattr(log, "duration_minutes_actual", None)
            tss_ac = getattr(log, "tss_actual", None)
            stype = getattr(log, "session_type_real", None) or ""

            dur_str = f"{dur_ac}min" if dur_ac else "—"
            tss_str = f"TSS {tss_ac:.0f}" if tss_ac else ""
            type_str = f" · {stype}" if stype and stype != "unknown" else ""
            lines.append(
                f"  🔄 {dow_short} {date_str} · {t('llm.week_pairs.bonus_activity')} · "
                f"{dur_str} · {tss_str}{type_str}"
            )

    return lines


def build_system_prompt(
    first_name: str,
    profile: AthleteProfileSchema,
    metrics: FitnessMetrics | None,
    recent_logs: list,
    plan: TrainingPlanSchema | None,
    today: date,
    fitness_as_of: date | None = None,
    fitness_is_stale: bool = False,
    session_logs: list | None = None,
    coach_memory: list | None = None,
    athlete_notes: dict | None = None,
    calendar_divergence: str | None = None,
    guardrail_findings: list | None = None,
    recovery_insufficiency: str | None = None,
    wellness_today: object | None = None,
    recovery_index: float | None = None,
    detected_phase: object | None = None,
    training_summary: list[str] | None = None,
) -> str:
    p = profile

    # Profil de base
    now_paris = datetime.now(ZoneInfo("Europe/Paris"))
    not_provided = t("llm.profile.not_provided")
    ftp_str = f"{p.equipment.ftp}W" if p.equipment.ftp else not_provided
    goal_str = _goal_label(p.objective.type)
    target_str = (
        p.objective.target_date.strftime("%d/%m/%Y") if p.objective.target_date
        else t("llm.profile.not_set")
    )
    days_str = ", ".join(p.availability.preferred_days)
    weight_str = f"{p.weight_kg:.1f} kg" if p.weight_kg else not_provided
    lthr_est = int(p.physio.hr_rest + 0.88 * (p.physio.hr_max - p.physio.hr_rest))
    hr_zones = compute_hr_zones(p.physio.hr_max, p.physio.hr_rest)
    zones_str = " | ".join(
        f"{code} {z.lower_bpm}–{z.upper_bpm}"
        for code, z in list(hr_zones.items())[:5]  # Z1-Z5
    )
    coaching_mode_label = (
        t("llm.profile.coaching_mode_power") if p.coaching_mode == "power"
        else t("llm.profile.coaching_mode_hr")
    )

    lines = [
        t("llm.profile.header", first_name=first_name),
        t("llm.profile.level_line", level=_level_label(p.level), goal=goal_str, target=target_str),
        t(
            "llm.profile.physio_line", ftp=ftp_str, hr_max=p.physio.hr_max,
            hr_rest=p.physio.hr_rest, lthr=lthr_est,
        ),
        t(
            "llm.profile.availability_line", weight=weight_str,
            hours=p.availability.hours_per_week, days=days_str,
        ),
        t("llm.profile.coaching_mode_line", mode=coaching_mode_label),
        t("llm.profile.hr_zones_line", zones=zones_str),
    ]

    # Blessure active
    injury = getattr(p, "injury_status", None)
    if injury and injury.get("is_injured"):
        loc = _body_location_label(injury.get("location", ""))
        sev = _severity_label(injury.get("severity", ""))
        restrictions = injury.get("zone_restrictions", {})
        restr_str = (
            ", ".join(f"{k}→{v}" for k, v in restrictions.items())
            if restrictions else t("llm.profile.no_restrictions")
        )
        lines += [
            "",
            t("llm.profile.active_injury", location=loc, severity=sev, restrictions=restr_str),
        ]

    # Mémoire coach — texte écrit par le LLM lui-même (outil update_coach_memory) et
    # réinjecté tel quel à chaque tour futur : encadré entre marqueurs anti-injection
    # (app/llm/prompt_fence.py), jamais interpolé brut dans le prompt.
    _memory = coach_memory or []
    _notes = athlete_notes or {}
    if _memory or _notes:
        mem_lines = [t("llm.coach_memory.header")]
        for m in sorted(_memory, key=lambda x: x.get("date", ""), reverse=True)[:5]:
            note = sanitize_untrusted_text(m.get("note", ""))
            mem_lines.append(f"• [{m.get('date','')}] {m.get('category','')} — {note}")
        if _notes:
            mem_lines.append(t("llm.coach_memory.athlete_notes_header"))
            for k, v in _notes.items():
                if v:
                    key = sanitize_untrusted_text(str(k))
                    val = sanitize_untrusted_text(str(v))
                    mem_lines.append(f"• {key} : {val}")
        lines.append("")
        lines.extend(wrap_untrusted_block(mem_lines))

    # Métriques de forme — TSB montré en chiffre nu, sans libellé narratif (trouvé en
    # test live 2026-09-21, voir CLAUDE.md § "TSB seul ne suffit pas") : `tsb_label()`
    # ("✨ Forme de pointe" etc.) suppose un athlète qui s'entraîne régulièrement et
    # amorce un affûtage — il ne sait pas distinguer ça d'un TSB gonflé par une coupure,
    # et son vocabulaire ("Pic de forme") se confond avec celui de la phase prescriptive
    # du plan, renforçant à tort la même lecture au lieu de la contredire. La phase
    # détectée ci-dessous, elle, est calculée sur le comportement réel — c'est elle qui
    # porte la lecture qualitative, pas le TSB seul.
    if metrics:
        as_of_note = (
            t("llm.fitness.source_note", date=fitness_as_of.strftime("%d/%m"))
            if fitness_as_of is not None
            else t("llm.fitness.local_estimate")
        )
        if fitness_is_stale:
            as_of_note += t("llm.fitness.no_newer_data")
        lines += [
            "",
            t("llm.fitness.header"),
            t("llm.fitness.metrics_line", ctl=f"{metrics.ctl:.0f}", atl=f"{metrics.atl:.0f}", tsb=f"{metrics.tsb:+.0f}"),
            as_of_note,
        ]
    else:
        lines += ["", t("llm.fitness.no_data")]

    if detected_phase is not None:
        from app.llm.narrator import (
            phase_label as _phase_label,  # même vocabulaire que week.phase (prescriptif)
        )

        phase_lbl = _phase_label(detected_phase.detected_phase)
        agree_note = ""
        if detected_phase.streams_agree is False:
            secondary_label = _phase_label(detected_phase.secondary_phase)
            agree_note = t("llm.detected_phase_agree_note", phase=secondary_label)
        lines.append(t("llm.detected_phase_line", phase=phase_lbl, agree_note=agree_note))

    if recovery_index is not None:
        lines.append(t("llm.recovery_index_line", value=f"{recovery_index:.2f}"))

    if training_summary:
        lines += ["", t("llm.recent_load.header")]
        lines.extend(f"- {line}" for line in training_summary)

    # Wellness qualitatif du jour — sous-ensemble volontairement restreint (sommeil,
    # fatigue, stress, mood, motivation) parmi les ~31 champs bruts désormais stockés
    # (app/db/models/wellness.py) ; le reste (macros, spO2, tension...) attend un besoin
    # réel avant d'être injecté ici (2026-09-21).
    if wellness_today is not None:
        w_parts = []
        if wellness_today.sleep_quality is not None:
            w_parts.append(t("llm.wellness.sleep_quality", value=wellness_today.sleep_quality))
        if wellness_today.sleep_score is not None:
            w_parts.append(t("llm.wellness.sleep_score", value=wellness_today.sleep_score))
        if wellness_today.fatigue is not None:
            w_parts.append(t("llm.wellness.fatigue", value=wellness_today.fatigue))
        if wellness_today.stress is not None:
            w_parts.append(t("llm.wellness.stress", value=wellness_today.stress))
        if wellness_today.mood is not None:
            w_parts.append(t("llm.wellness.mood", value=wellness_today.mood))
        if wellness_today.motivation is not None:
            w_parts.append(t("llm.wellness.motivation", value=wellness_today.motivation))
        if w_parts:
            lines.append(t("llm.wellness.today_line", parts=" | ".join(w_parts)))

    # Recent sessions (SessionLog or pre-plan Activity), already sorted and bounded by caller.
    # Deux faits rendus explicites plutôt que laissés à déduire (trouvé en test live
    # 2026-09-21, voir CLAUDE.md) : l'écart en jours depuis la séance précédente — un
    # LLM ne fait pas fiablement l'arithmétique de dates tout seul, donc un trou de 11
    # jours entre deux lignes passait inaperçu — et le taux de ressenti renseigné, pour
    # qu'un manque de RPE massif soit un chiffre visible plutôt qu'une série de tirets
    # qu'on peut glisser dessus sans y prêter attention.
    if recent_logs:
        one_session = len(recent_logs) == 1
        heading = (
            t("llm.recent_sessions.single_heading") if one_session
            else t("llm.recent_sessions.multi_heading", count=len(recent_logs))
        )
        lines += ["", t("llm.section_header_colon", label=heading)]
        rpe_known = 0
        previous_date = None
        for item in recent_logs:
            if hasattr(item, "logged_date"):  # SessionLog
                item_date = item.logged_date
                dur_str = f"{item.duration_minutes_actual}min" if item.duration_minutes_actual else "—"
                rpe_str = rpe_label(item.rpe) if item.rpe is not None else "—"
                if item.rpe is not None:
                    rpe_known += 1
                tss_str = f"{item.tss_actual:.0f}" if item.tss_actual else "—"
                hr_str = f"{item.avg_heart_rate}bpm" if item.avg_heart_rate else "—"
                pw_str = f"{item.avg_power}W" if item.avg_power else "—"
                env_str = f" ({item.environment})" if getattr(item, "environment", None) else ""
            else:  # Activity (importée, pré-plan) — jamais de RPE côté source
                item_date = item.activity_date
                dur_str = f"{item.duration_seconds // 60}min" if item.duration_seconds else "—"
                rpe_str = "—"
                tss_str = f"{item.tss:.0f}" if item.tss else "—"
                hr_str = f"{int(item.avg_heartrate)}bpm" if item.avg_heartrate else "—"
                pw_str = f"{int(item.avg_watts)}W" if item.avg_watts else "—"
                env_str = f" ({item.environment})" if item.environment else ""
            gap = (item_date - previous_date).days if previous_date is not None else None
            gap_str = t("llm.recent_sessions.gap_suffix", days=gap) if gap is not None else ""
            previous_date = item_date
            lines.append(t(
                "llm.recent_sessions.line",
                date=item_date.strftime("%d/%m"), gap=gap_str, duration=dur_str,
                rpe=rpe_str, tss=tss_str, hr=hr_str, power=pw_str, env=env_str,
            ))
        if not one_session:
            lines.append(t(
                "llm.recent_sessions.rpe_coverage", known=rpe_known, total=len(recent_logs)
            ))
    else:
        lines += ["", t("llm.recent_sessions.none")]

    # Semaine courante du plan — avec paires plan/réalisé si session_logs fourni
    if plan and plan.start_date:
        week_num = (today - plan.start_date).days // 7 + 1
        current_week = next((w for w in plan.weeks if w.week_number == week_num), None)
        if current_week:
            lines.append("")
            if session_logs is not None:
                from app.providers.analysis.matching import build_activity_session_pairs
                pairs = build_activity_session_pairs(plan, plan.start_date, session_logs, week_num)
                lines += _format_week_pairs(pairs, week_num, current_week.phase, today)
            else:
                # Fallback : affichage plan seul (sans données réalisées)
                lines.append(t(
                    "llm.current_week.fallback_header", week=week_num, phase=current_week.phase
                ))
                week_start = plan.start_date + timedelta(weeks=week_num - 1)
                for sess in sorted(current_week.sessions, key=lambda s: s.day_of_week):
                    day_name = _day_name(sess.day_of_week)
                    session_date = week_start + timedelta(days=sess.day_of_week)
                    wtype = _workout_label(sess.workout_type)
                    lines.append(t(
                        "llm.current_week.session_line",
                        day=day_name, date=session_date.strftime("%d/%m"), type=wtype,
                        zone=sess.zone_code, duration=sess.duration_minutes,
                        tss=f"{sess.tss_target:.0f}",
                    ))

    if calendar_divergence:
        lines += ["", calendar_divergence]

    # Signaux des garde-fous (spec 006) — calculés par le moteur déterministe, le LLM
    # ne fait que les restituer (FR-022). Chaque signal porte sa valeur observée, sa
    # référence, et une action concrète (SC-003).
    if guardrail_findings:
        lines += ["", t("llm.guardrails.header")]
        for f in guardrail_findings:
            lines.append(t(
                "llm.guardrails.finding_line",
                observed=f.observed, reference=f.reference, threshold=f.threshold, action=f.action,
            ))
        lines.append(t("llm.guardrails.instruction"))

    if recovery_insufficiency:
        lines += ["", f"ℹ️ {recovery_insufficiency}"]

    # Donnée la plus volatile (précision minute, change à chaque appel) — en dernier
    # pour maximiser la portion du prompt identique d'un appel à l'autre (cache
    # automatique côté OpenRouter/DeepSeek : la boucle de chat principale y appelle
    # toujours ce provider, cf. app/llm/chat_client.py — pas de breakpoint explicite
    # nécessaire, juste un préfixe stable). Le bloc d'identité coach, lui, est 100 %
    # statique et vit désormais dans build_ux_system_prompt (préfixe stable).
    lines += ["", *_temporal_reference_rules(now_paris)]
    lines.append(
        f"📅 {_day_name(now_paris.weekday())} {now_paris.strftime('%d/%m/%Y — %H:%M')}"
    )

    return "\n".join(lines)


def build_context_messages(chat_history: list) -> list[dict]:
    """Convertit l'historique DB en format messages OpenAI."""
    return [{"role": msg.role, "content": msg.content} for msg in chat_history]
