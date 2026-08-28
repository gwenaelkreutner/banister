"""
Définitions des outils LLM (format OpenAI-compatible) pour le chat agentique.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.engine.atl_ctl import FitnessMetrics, compute_fitness, tsb_label
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.engine.zones import compute_hr_zones
from app.llm.prompts import COACH_SOUL

# ── Schémas des outils (format OpenAI tool_use) ──────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_sessions",
            "description": (
                "Récupère les séances planifiées dans les prochains jours. "
                "Utilise cet outil quand l'athlète demande son programme, "
                "sa séance de demain, ou ce qu'il doit faire ce week-end."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Nombre de jours à récupérer (1 à 14).",
                        "minimum": 1,
                        "maximum": 14,
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
            "description": (
                "Enregistre une blessure ou douleur signalée par l'athlète. "
                "Met à jour le profil et adapte automatiquement le plan "
                "(semaine courante + 2 semaines suivantes avec reprise progressive). "
                "Utilise cet outil dès que l'athlète mentionne une douleur, gêne ou blessure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "enum": ["knee", "back", "shoulder", "hip", "ankle", "other"],
                        "description": "Zone corporelle blessée.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["mild", "moderate", "severe"],
                        "description": "mild=légère, moderate=modérée, severe=sévère.",
                    },
                    "estimated_recovery_days": {
                        "type": "integer",
                        "description": "Estimation de jours de récupération (ex: 7, 14, 21).",
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
            "description": (
                "Propose une modification du plan d'entraînement basée sur la situation de l'athlète. "
                "La modification n'est PAS appliquée immédiatement — elle sera soumise à la confirmation "
                "de l'athlète via des boutons. Utilise cet outil quand l'athlète demande à alléger la charge "
                "sur une semaine entière, ou a un événement qui change son planning sur plusieurs jours."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": ["fatigue", "injury", "event", "preference", "illness"],
                        "description": "Raison de la modification.",
                    },
                    "modification_type": {
                        "type": "string",
                        "enum": ["reduce_intensity", "reduce_volume", "skip_session", "swap_to_recovery"],
                        "description": "Type d'ajustement à apporter au plan.",
                    },
                    "week_offset": {
                        "type": "integer",
                        "description": "0 = semaine courante, 1 = semaine prochaine.",
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
            "description": (
                "Propose un ajustement sur une séance précise d'un seul jour (nécessite confirmation). "
                "Utilise cet outil pour une contrainte ponctuelle : réunion, météo, fatigue du jour. "
                "Pour une contrainte qui touche plusieurs jours ou une semaine entière, utilise propose_plan_modification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "constraint_type": {
                        "type": "string",
                        "enum": ["meeting", "weather_bad", "tired_today", "personal"],
                    },
                    "day_offset": {
                        "type": "integer",
                        "description": "0=aujourd'hui, 1=demain.",
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
            "description": (
                "Mémorise une observation DURABLE sur l'athlète. "
                "Utilise quand il révèle un pattern de fatigue récurrent, une contrainte physique "
                "confirmée, une préférence de communication, un événement marquant. "
                "Maximum 1 appel par conversation. "
                "NE PAS utiliser pour des états transitoires (fatigue du jour, météo, humeur passagère)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add_note", "update_athlete_notes"],
                        "description": (
                            "add_note : ajoute/remplace une note dans coach_memory. "
                            "update_athlete_notes : met à jour une clé stable dans athlete_notes."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "enum": ["fatigue", "motivation", "physique", "event", "preference"],
                        "description": "Catégorie de la note — requis pour add_note.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Texte libre, max 120 chars — requis pour add_note.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Clé dans athlete_notes — requis pour update_athlete_notes.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Valeur à enregistrer — requis pour update_athlete_notes.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


# ── Construction du system prompt ─────────────────────────────────────────────

DAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
WORKOUT_FR = {
    "long_ride": "Sortie longue",
    "intervals": "Intervalles",
    "endurance": "Endurance",
    "recovery": "Récupération",
}
LEVEL_FR = {
    "beginner": "Débutant",
    "intermediate": "Intermédiaire",
    "advanced": "Avancé",
    "expert": "Expert",
}
GOAL_FR = {
    "event": "Événement cible",
    "fitness": "Forme générale / Bien-être",
    "performance": "Performance",
    "other": "Objectif personnel",
}
SEVERITY_FR = {"mild": "légère", "moderate": "modérée", "severe": "sévère"}
LOCATION_FR = {
    "knee": "genou", "back": "dos", "shoulder": "épaule",
    "hip": "hanche", "ankle": "cheville", "other": "autre zone",
}


def _format_week_pairs(pairs: list, week_num: int, phase: str, today: date) -> list[str]:
    """Formate les paires plan/réalisé pour le system prompt."""
    lines = [f"SEMAINE {week_num} — PLAN & RÉALISÉ (phase {phase}) :"]

    _RPE_EMOJI = {"hard": "😫", "normal": "😐", "easy": "🙂"}

    for pair in pairs:
        dow_short = DAY_NAMES_FR[pair.day_of_week][:3]
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
            rpe = getattr(log, "rpe_emoji", None)
            elev = getattr(log, "elevation_gain_m", None)
            group = (getattr(log, "athlete_count", 1) or 1) > 1

            # Date réelle du log (peut différer de la date planifiée si décalage)
            actual_date = getattr(log, "logged_date", pair.planned_date)
            if actual_date != pair.planned_date:
                actual_dow = DAY_NAMES_FR[actual_date.weekday()][:3]
                date_label = f"{actual_dow} {actual_date.strftime('%d/%m')} (plan {date_str})"
            else:
                date_label = f"{dow_short} {date_str}"

            dur_str = f"{dur_pl}→{dur_ac}min" if dur_ac else f"{dur_pl}min prévues"
            if tss_ac and tss_pl:
                diff_pct = (tss_ac / tss_pl - 1) * 100
                tss_str = f"TSS {tss_pl}→{tss_ac:.0f} ({diff_pct:+.0f}%)"
            else:
                tss_str = f"TSS {tss_pl} prévu"

            extras = []
            if stype and stype != "unknown":
                extras.append(stype)
            if dzone:
                zone_flag = " ⚠️" if dzone != spec.zone_code else ""
                extras.append(f"zone {dzone}{zone_flag}")
            if elev and elev > 500:
                extras.append(f"{elev:.0f}m D+")
            if group:
                extras.append("groupe")
            if rpe:
                extras.append(f"RPE {_RPE_EMOJI.get(rpe, rpe)}")

            wtype = WORKOUT_FR.get(spec.workout_type, spec.workout_type)
            extras_str = f" · {' · '.join(extras)}" if extras else ""
            lines.append(f"  ✅ {date_label} · {wtype} {spec.zone_code} · {dur_str} · {tss_str}{extras_str}")

        elif pair.session_spec and not pair.session_log:
            # ── Séance planifiée non réalisée ──────────────────────────────
            spec = pair.session_spec
            wtype = WORKOUT_FR.get(spec.workout_type, spec.workout_type)
            if pair.planned_date <= today:
                lines.append(
                    f"  ❌ {dow_short} {date_str} · {wtype} {spec.zone_code} — "
                    f"{spec.duration_minutes}min (TSS {round(spec.tss_target)}) — non réalisée"
                )
            else:
                lines.append(
                    f"  📅 {dow_short} {date_str} · {wtype} {spec.zone_code} — "
                    f"{spec.duration_minutes}min (TSS {round(spec.tss_target)}) — à venir"
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
                f"  🔄 {dow_short} {date_str} · Activité bonus · {dur_str} · {tss_str}{type_str}"
            )

    return lines


def build_system_prompt(
    first_name: str,
    profile: AthleteProfileSchema,
    metrics: FitnessMetrics | None,
    recent_logs: list,
    plan: TrainingPlanSchema | None,
    today: date,
    session_logs: list | None = None,
    coach_memory: list | None = None,
    athlete_notes: dict | None = None,
    calendar_divergence: str | None = None,
    guardrail_findings: list | None = None,
    recovery_insufficiency: str | None = None,
) -> str:
    p = profile

    # Profil de base
    now_paris = datetime.now(ZoneInfo("Europe/Paris"))
    ftp_str = f"{p.equipment.ftp}W" if p.equipment.ftp else "non renseigné"
    goal_str = GOAL_FR.get(p.objective.type, p.objective.type)
    target_str = p.objective.target_date.strftime("%d/%m/%Y") if p.objective.target_date else "non fixée"
    days_str = ", ".join(p.availability.preferred_days)
    weight_str = f"{p.weight_kg:.1f} kg" if p.weight_kg else "non renseigné"
    lthr_est = int(p.physio.hr_rest + 0.88 * (p.physio.hr_max - p.physio.hr_rest))
    hr_zones = compute_hr_zones(p.physio.hr_max, p.physio.hr_rest)
    zones_str = " | ".join(
        f"{code} {z.lower_bpm}–{z.upper_bpm}"
        for code, z in list(hr_zones.items())[:5]  # Z1-Z5
    )

    lines = [
        f"📅 {DAY_NAMES_FR[now_paris.weekday()]} {now_paris.strftime('%d/%m/%Y — %H:%M')}",
        "",
        f"PROFIL ATHLÈTE — {first_name} :",
        f"- Niveau : {LEVEL_FR.get(p.level, p.level)} | Objectif : {goal_str} (date cible : {target_str})",
        f"- FTP : {ftp_str} | FC max : {p.physio.hr_max} bpm | FC repos : {p.physio.hr_rest} bpm | LTHR ~{lthr_est} bpm (estimé)",
        f"- Poids : {weight_str} | Volume : {p.availability.hours_per_week}h/semaine | Jours préférés : {days_str}",
        f"- Mode coaching : {'puissance' if p.coaching_mode == 'power' else 'fréquence cardiaque'}",
        f"- Zones FC (bpm) : {zones_str}",
    ]

    # Blessure active
    injury = getattr(p, "injury_status", None)
    if injury and injury.get("is_injured"):
        loc = LOCATION_FR.get(injury.get("location", ""), injury.get("location", ""))
        sev = SEVERITY_FR.get(injury.get("severity", ""), injury.get("severity", ""))
        restrictions = injury.get("zone_restrictions", {})
        restr_str = ", ".join(f"{k}→{v}" for k, v in restrictions.items()) if restrictions else "aucune"
        lines += [
            "",
            f"⚠️ BLESSURE ACTIVE : {loc} ({sev}) — zones restreintes : {restr_str}",
        ]

    # Mémoire coach
    _memory = coach_memory or []
    _notes = athlete_notes or {}
    if _memory or _notes:
        lines.append("")
        lines.append("MÉMOIRE COACH :")
        for m in sorted(_memory, key=lambda x: x.get("date", ""), reverse=True)[:5]:
            lines.append(f"• [{m.get('date','')}] {m.get('category','')} — {m.get('note','')}")
        if _notes:
            lines.append("NOTES ATHLÈTE :")
            for k, v in _notes.items():
                if v:
                    lines.append(f"• {k} : {v}")

    # Métriques de forme
    if metrics:
        label = tsb_label(metrics.tsb)
        lines += [
            "",
            "FORME ACTUELLE :",
            f"CTL {metrics.ctl:.0f} (fitness) | ATL {metrics.atl:.0f} (fatigue) | TSB {metrics.tsb:+.0f} {label}",
        ]
    else:
        lines += ["", "FORME ACTUELLE : pas encore de données (aucune séance loggée)."]

    # 7 dernières séances (SessionLog ou Activity pré-plan, déjà triés et limités à 7)
    if recent_logs:
        lines += ["", "7 DERNIÈRES SÉANCES :"]
        for item in recent_logs:
            if hasattr(item, "logged_date"):  # SessionLog
                item_date = item.logged_date
                dur_str = f"{item.duration_minutes_actual}min" if item.duration_minutes_actual else "—"
                rpe_str = {"hard": "😫", "normal": "😐", "easy": "🙂"}.get(item.rpe_emoji or "", "—")
                tss_str = f"{item.tss_actual:.0f}" if item.tss_actual else "—"
                hr_str = f"{item.avg_heart_rate}bpm" if item.avg_heart_rate else "—"
                pw_str = f"{item.avg_power}W" if item.avg_power else "—"
                env_str = f" ({item.environment})" if getattr(item, "environment", None) else ""
            else:  # Activity (importée, pré-plan)
                item_date = item.activity_date
                dur_str = f"{item.duration_seconds // 60}min" if item.duration_seconds else "—"
                rpe_str = "—"
                tss_str = f"{item.tss:.0f}" if item.tss else "—"
                hr_str = f"{int(item.avg_heartrate)}bpm" if item.avg_heartrate else "—"
                pw_str = f"{int(item.avg_watts)}W" if item.avg_watts else "—"
                env_str = f" ({item.environment})" if item.environment else ""
            lines.append(
                f"- {item_date.strftime('%d/%m')} | {dur_str} | RPE {rpe_str} | TSS {tss_str} | {hr_str} | {pw_str}{env_str}"
            )
    else:
        lines += ["", "7 DERNIÈRES SÉANCES : aucune séance enregistrée."]

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
                lines.append(f"SEMAINE EN COURS (S{week_num} — phase {current_week.phase}) :")
                week_start = plan.start_date + timedelta(weeks=week_num - 1)
                for sess in sorted(current_week.sessions, key=lambda s: s.day_of_week):
                    day_name = DAY_NAMES_FR[sess.day_of_week]
                    session_date = week_start + timedelta(days=sess.day_of_week)
                    wtype = WORKOUT_FR.get(sess.workout_type, sess.workout_type)
                    lines.append(f"  {day_name} {session_date.strftime('%d/%m')} : {wtype} {sess.zone_code} — {sess.duration_minutes}min (TSS cible {sess.tss_target:.0f})")

    if calendar_divergence:
        lines += ["", calendar_divergence]

    # Signaux des garde-fous (spec 006) — calculés par le moteur déterministe, le LLM
    # ne fait que les restituer (FR-022). Chaque signal porte sa valeur observée, sa
    # référence, et une action concrète (SC-003).
    if guardrail_findings:
        lines += ["", "⚠️ SIGNAUX D'ENTRAÎNEMENT (à transmettre tels quels, ne recalcule rien) :"]
        for f in guardrail_findings:
            lines.append(
                f"  • {f.observed} (référence {f.reference} ; seuil {f.threshold})\n"
                f"    → {f.action}"
            )
        lines.append(
            "Si l'athlète discute d'entraînement, mentionne le ou les signaux ci-dessus "
            "avec leur chiffre et l'action associée — c'est le cœur du métier de coach ici."
        )

    if recovery_insufficiency:
        lines += ["", f"ℹ️ {recovery_insufficiency}"]

    lines.append("")
    lines.append(COACH_SOUL.format(first_name=first_name))

    return "\n".join(lines)


def build_context_messages(chat_history: list) -> list[dict]:
    """Convertit l'historique DB en format messages OpenAI."""
    return [{"role": msg.role, "content": msg.content} for msg in chat_history]
