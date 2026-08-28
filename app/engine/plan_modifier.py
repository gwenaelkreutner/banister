"""
Moteur déterministe de modification de plan.

Appelé par les outils LLM — garantit la cohérence mathématique
(règle 48h, progression des zones, TSS).
"""

import uuid
from datetime import date, timedelta

from app.engine.schemas import (
    RepeatGroup,
    SessionSpec,
    Step,
    TrainingPlanSchema,
    WeekPlan,
    derive_duration_minutes,
    derive_target_time_in_zone_minutes,
)

# Zones dans l'ordre croissant d'intensité
_ZONE_ORDER = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]
_HIGH_INTENSITY = {"Z4", "Z5", "Z6"}

_DAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_DAY_NAME_TO_INT = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Facteurs de réduction TSS selon le type de modification
_REDUCTION_FACTORS = {
    "reduce_intensity": 0.70,   # -30% TSS
    "reduce_volume": 0.60,      # -40% TSS
    "skip_session": 0.0,        # séance supprimée
    "swap_to_recovery": 0.50,   # tout passe en Z1/Z2
}


# ── Steps : mise à l'échelle et transit par proposition JSON (spec 004 T018-T020) ──


def _scale_steps(
    steps: list[Step | RepeatGroup] | None,
    factor: float,
    zone_remap: dict[str, str] | None = None,
) -> list[Step | RepeatGroup] | None:
    """Scale chaque step de `factor` (minimum 1 minute) et remappe les zones selon
    `zone_remap`. Retourne None si `steps` est None — une séance legacy le reste,
    aucune structure n'est fabriquée pour elle (FR-014).

    Mise à l'échelle proportionnelle simple, pas du fitting qui « préserve le
    caractère structurel » (c'est le rôle d'app/engine/fitting.py, Phase 7) — les
    ajustements ad hoc de ce module appliquaient déjà un `int(x * factor)` plat sur
    le résumé ; ceci garde la même philosophie pour les steps plutôt que d'en
    introduire une seconde."""
    if steps is None:
        return None
    zone_remap = zone_remap or {}

    def _scale_one(s: Step) -> Step:
        return Step(
            kind=s.kind,
            duration_minutes=max(1, round(s.duration_minutes * factor)),
            zone_code=zone_remap.get(s.zone_code, s.zone_code),
        )

    result: list[Step | RepeatGroup] = []
    for item in steps:
        if isinstance(item, RepeatGroup):
            scaled_inner = [_scale_one(s) for s in item.steps]
            result.append(RepeatGroup(repeat=item.repeat, steps=scaled_inner))
        else:
            result.append(_scale_one(item))
    return result


def _steps_to_json(steps: list[Step | RepeatGroup] | None) -> list[dict] | None:
    """Sérialise les steps pour transiter dans un dict de proposition (affiché à
    l'utilisateur puis renvoyé tel quel à apply_proposed_modification)."""
    if steps is None:
        return None
    return [item.model_dump(mode="json") for item in steps]


def _steps_from_json(data: list[dict] | None) -> list[Step | RepeatGroup] | None:
    """Reconstruit les steps depuis leur forme JSON — un RepeatGroup se distingue
    d'un Step par la présence de la clé `repeat`."""
    if data is None:
        return None
    result: list[Step | RepeatGroup] = []
    for item in data:
        if "repeat" in item:
            result.append(RepeatGroup.model_validate(item))
        else:
            result.append(Step.model_validate(item))
    return result


def adapt_plan_for_injury(plan, injury_data: dict) -> dict:
    """
    Adapte le plan en fonction d'une blessure.
    Portée : semaine courante + 2 suivantes (reprise progressive).
    Retourne un dict avec les semaines modifiées (pour affichage).
    """
    if not plan or not plan.start_date:
        return {"modified_weeks": []}

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    zone_restrictions: dict = injury_data.get("zone_restrictions", {})
    today = date.today()
    current_week = (today - plan.start_date).days // 7 + 1

    modified_weeks = []

    # Facteurs de reprise progressive sur 3 semaines
    # S0 (blessure) = 50%, S+1 = 70%, S+2 = 90%
    recovery_factors = {0: 0.50, 1: 0.70, 2: 0.90}

    for week_offset, factor in recovery_factors.items():
        target_week = current_week + week_offset
        week = next((w for w in schema.weeks if w.week_number == target_week), None)
        if week is None:
            continue

        new_sessions = []
        for sess in week.sessions:
            new_zone = zone_restrictions.get(sess.zone_code, sess.zone_code)
            new_tss = round(sess.tss_target * factor, 1)
            zone_remap = {sess.zone_code: new_zone} if new_zone != sess.zone_code else None
            new_steps = _scale_steps(sess.steps, factor, zone_remap)
            new_sess = SessionSpec(
                day_of_week=sess.day_of_week,
                workout_type=sess.workout_type if new_zone not in ("Z5", "Z6") else "endurance",
                zone_code=new_zone,
                duration_minutes=(
                    derive_duration_minutes(new_steps) if new_steps is not None
                    else max(20, int(sess.duration_minutes * factor))
                ),
                target_time_in_zone_minutes=(
                    derive_target_time_in_zone_minutes(new_steps) if new_steps is not None
                    else int(sess.target_time_in_zone_minutes * factor)
                ),
                tss_target=new_tss,
                description_fr=f"{sess.description_fr} [adapté blessure {week_offset+1}/3]",
                steps=new_steps,
            )
            new_sessions.append(new_sess)

        modified_weeks.append({
            "week_number": target_week,
            "offset": week_offset,
            "changes": [
                {
                    "day": sess.day_of_week,
                    "original_zone": orig.zone_code,
                    "new_zone": new.zone_code,
                    "original_tss": orig.tss_target,
                    "new_tss": new.tss_target,
                }
                for orig, new in zip(week.sessions, new_sessions)
                if orig.zone_code != new.zone_code or orig.tss_target != new.tss_target
            ],
        })

        # Mettre à jour le schéma en mémoire
        week_idx = next(i for i, w in enumerate(schema.weeks) if w.week_number == target_week)
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=True,
            total_tss_target=round(sum(s.tss_target for s in new_sessions), 1),
            sessions=new_sessions,
            start_date=week.start_date,
        )

    # Sauvegarder en DB
    plan.plan_technical = schema.model_dump(mode="json")

    return {"modified_weeks": modified_weeks}


def propose_week_adjustment(plan, week_offset: int, modification_type: str) -> dict:
    """
    Calcule une proposition de modification pour une semaine donnée.
    Ne modifie PAS le plan — retourne une proposition pour confirmation utilisateur.
    """
    if not plan or not plan.start_date:
        return {"error": "Pas de plan actif ou date de début manquante."}

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    today = date.today()
    current_week_num = (today - plan.start_date).days // 7 + 1
    target_week_num = current_week_num + week_offset

    week = next((w for w in schema.weeks if w.week_number == target_week_num), None)
    if week is None:
        return {"error": f"Semaine {target_week_num} introuvable dans le plan."}

    factor = _REDUCTION_FACTORS.get(modification_type, 0.70)

    before_sessions = []
    after_sessions = []

    for sess in week.sessions:
        before_sessions.append({
            "day": sess.day_of_week,
            "workout_type": sess.workout_type,
            "zone": sess.zone_code,
            "duration_minutes": sess.duration_minutes,
            "tss_target": round(sess.tss_target),
        })

        if modification_type == "skip_session" and sess.zone_code in _HIGH_INTENSITY:
            # On ne supprime que les séances intensives
            continue
        elif modification_type == "swap_to_recovery":
            new_zone = "Z2"
            new_tss = round(sess.tss_target * factor, 1)
        else:
            # Réduire l'intensité d'un cran
            new_zone = _downgrade_zone(sess.zone_code)
            new_tss = round(sess.tss_target * factor, 1)

        dur_comp = _downgrade_duration_factor(sess.zone_code) if new_zone != sess.zone_code else 1.0
        effective_factor = max(factor, 0.6) * dur_comp
        zone_remap = {sess.zone_code: new_zone} if new_zone != sess.zone_code else None
        new_steps = _scale_steps(sess.steps, effective_factor, zone_remap)
        after_sessions.append({
            "day": sess.day_of_week,
            "workout_type": sess.workout_type if new_zone not in _HIGH_INTENSITY else "endurance",
            "zone": new_zone,
            "duration_minutes": (
                derive_duration_minutes(new_steps) if new_steps is not None
                else max(20, int(sess.duration_minutes * effective_factor))
            ),
            "target_time_in_zone_minutes": (
                derive_target_time_in_zone_minutes(new_steps) if new_steps is not None
                else int(sess.target_time_in_zone_minutes * effective_factor)
            ),
            "tss_target": new_tss,
            "steps": _steps_to_json(new_steps),
        })

    proposal_id = str(uuid.uuid4())

    return {
        "proposal_id": proposal_id,
        "week_number": target_week_num,
        "week_offset": week_offset,
        "modification_type": modification_type,
        "summary": _build_modification_summary(modification_type, week_offset),
        "before_sessions": before_sessions,
        "after_sessions": after_sessions,
        "tss_before": round(week.total_tss_target),
        "tss_after": round(sum(s["tss_target"] for s in after_sessions)),
        "requires_confirmation": True,
    }


def apply_proposed_modification(plan, proposal: dict) -> bool:
    """
    Applique une proposition de modification au plan.
    Modifie `plan.plan_technical` en place.
    Retourne True si succès.
    """
    if not plan or not plan.start_date:
        return False

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    target_week_num = proposal.get("week_number")
    after_sessions_data = proposal.get("after_sessions", [])
    modification_type = proposal.get("modification_type", "reduce_intensity")
    week_offset = proposal.get("week_offset", 0)

    week = next((w for w in schema.weeks if w.week_number == target_week_num), None)
    if week is None:
        return False

    new_sessions = []
    for s in after_sessions_data:
        new_sessions.append(SessionSpec(
            day_of_week=s["day"],
            workout_type=s["workout_type"],
            zone_code=s["zone"],
            duration_minutes=s["duration_minutes"],
            target_time_in_zone_minutes=s.get("target_time_in_zone_minutes", 0),
            tss_target=s["tss_target"],
            description_fr=f"Séance ajustée ({modification_type})",
            steps=_steps_from_json(s.get("steps")),
        ))

    week_idx = next(i for i, w in enumerate(schema.weeks) if w.week_number == target_week_num)
    schema.weeks[week_idx] = WeekPlan(
        week_number=week.week_number,
        phase=week.phase,
        is_recovery_week=(modification_type in ("reduce_volume", "swap_to_recovery")),
        total_tss_target=round(sum(s.tss_target for s in new_sessions), 1),
        sessions=new_sessions,
        start_date=week.start_date,
    )

    # Reprise progressive sur les 2 semaines suivantes si semaine courante
    if week_offset == 0:
        _apply_progressive_recovery(schema, target_week_num)

    plan.plan_technical = schema.model_dump(mode="json")
    return True


def propose_session_adjustment(plan, target_date: date, action: str, available_days: list) -> dict:
    """
    Calcule une proposition de modification pour une séance spécifique (un jour précis).
    Ne modifie PAS le plan — retourne une proposition pour confirmation utilisateur.

    action ∈ {"skip", "shift", "reduce_50", "indoor"}
    available_days : liste de noms de jours en anglais minuscule ["monday", "wednesday", ...]
    """
    if not plan or not plan.start_date:
        return {"error": "Pas de plan actif ou date de début manquante."}

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    week_num = (target_date - plan.start_date).days // 7 + 1
    dow = target_date.weekday()  # 0=Lundi

    week = next((w for w in schema.weeks if w.week_number == week_num), None)
    if week is None:
        return {
            "no_session": True,
            "date": str(target_date),
            "message": "Cette date est hors de la période couverte par le plan.",
        }

    sess = next((s for s in week.sessions if s.day_of_week == dow), None)
    if sess is None:
        return {
            "no_session": True,
            "date": str(target_date),
            "message": f"Aucune séance prévue le {_DAY_NAMES_FR[dow]} ({target_date.strftime('%d/%m')}).",
        }

    session_before = {
        "date": str(target_date),
        "day": dow,
        "day_name": _DAY_NAMES_FR[dow],
        "workout_type": sess.workout_type,
        "zone": sess.zone_code,
        "duration_minutes": sess.duration_minutes,
        "tss_target": round(sess.tss_target),
        "description": sess.description_fr,
    }

    session_after = None
    summary_fr = ""

    if action == "skip":
        session_after = None
        summary_fr = (
            f"Supprimer la séance du {_DAY_NAMES_FR[dow]} "
            f"({sess.description_fr}, {sess.zone_code}, TSS {round(sess.tss_target)})"
        )

    elif action == "reduce_50":
        new_tss = round(sess.tss_target * 0.50, 1)
        new_duration = max(20, int(sess.duration_minutes * 0.65))
        session_after = {
            "date": str(target_date),
            "day": dow,
            "day_name": _DAY_NAMES_FR[dow],
            "workout_type": sess.workout_type,
            "zone": sess.zone_code,
            "duration_minutes": new_duration,
            "tss_target": new_tss,
            "description": sess.description_fr + " [Réduite −50%]",
        }
        summary_fr = (
            f"Réduire la séance du {_DAY_NAMES_FR[dow]} de moitié "
            f"({sess.duration_minutes}min → {new_duration}min, TSS {round(sess.tss_target)} → {round(new_tss)})"
        )

    elif action == "indoor":
        session_after = {
            "date": str(target_date),
            "day": dow,
            "day_name": _DAY_NAMES_FR[dow],
            "workout_type": sess.workout_type,
            "zone": sess.zone_code,
            "duration_minutes": sess.duration_minutes,
            "tss_target": round(sess.tss_target, 1),
            "description": sess.description_fr + " 🏠 [Intérieur — home trainer/rouleaux]",
        }
        summary_fr = (
            f"Adapter la séance du {_DAY_NAMES_FR[dow]} pour l'intérieur "
            f"(même charge, home trainer/rouleaux)"
        )

    elif action == "shift":
        avail_ints = sorted(
            _DAY_NAME_TO_INT[d] for d in (available_days or []) if d in _DAY_NAME_TO_INT
        )
        new_day = None
        new_date = None
        # Chercher le prochain créneau libre dans les 7 jours suivants
        for offset in range(1, 8):
            candidate_date = target_date + timedelta(days=offset)
            candidate_dow = candidate_date.weekday()
            if candidate_dow not in avail_ints:
                continue
            cand_week_num = (candidate_date - plan.start_date).days // 7 + 1
            cand_week = next((w for w in schema.weeks if w.week_number == cand_week_num), None)
            occupied = {s.day_of_week for s in cand_week.sessions} if cand_week else set()
            if candidate_dow in occupied:
                continue
            new_day = candidate_dow
            new_date = candidate_date
            break

        if new_day is None:
            return {
                "no_session": True,
                "date": str(target_date),
                "message": (
                    f"Impossible de trouver un créneau libre pour déplacer "
                    f"la séance du {_DAY_NAMES_FR[dow]}. Tous tes jours préférés sont occupés."
                ),
            }

        session_after = {
            "date": str(new_date),
            "day": new_day,
            "day_name": _DAY_NAMES_FR[new_day],
            "workout_type": sess.workout_type,
            "zone": sess.zone_code,
            "duration_minutes": sess.duration_minutes,
            "tss_target": round(sess.tss_target, 1),
            "description": sess.description_fr,
            "shifted_from_day": dow,
            "shifted_from_date": str(target_date),
        }
        summary_fr = (
            f"Déplacer la séance du {_DAY_NAMES_FR[dow]} ({target_date.strftime('%d/%m')}) "
            f"au {_DAY_NAMES_FR[new_day]} ({new_date.strftime('%d/%m')})"
        )
    else:
        return {"error": f"Action inconnue : {action}"}

    return {
        "proposal_id": str(uuid.uuid4()),
        "type": "session_adjustment",
        "target_date": str(target_date),
        "week_number": week_num,
        "action": action,
        "summary": summary_fr,
        "session_before": session_before,
        "session_after": session_after,
        "requires_confirmation": True,
    }


def apply_session_adjustment(plan, proposal: dict) -> bool:
    """
    Applique une proposition de modification de séance (session_adjustment).
    Modifie `plan.plan_technical` en place.
    Retourne True si succès.
    """
    if not plan or not plan.start_date:
        return False

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    target_date_str = proposal.get("target_date")
    week_num = proposal.get("week_number")
    action = proposal.get("action")
    session_after = proposal.get("session_after")

    if not target_date_str or not week_num or not action:
        return False

    target_date = date.fromisoformat(target_date_str)
    orig_dow = target_date.weekday()

    week = next((w for w in schema.weeks if w.week_number == week_num), None)
    if week is None:
        return False

    week_idx = next(i for i, w in enumerate(schema.weeks) if w.week_number == week_num)

    if action == "skip":
        new_sessions = [s for s in week.sessions if s.day_of_week != orig_dow]
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=week.is_recovery_week,
            total_tss_target=round(sum(s.tss_target for s in new_sessions), 1),
            sessions=new_sessions,
            start_date=week.start_date,
        )

    elif action == "reduce_50" and session_after:
        orig_sess = next((s for s in week.sessions if s.day_of_week == orig_dow), None)
        if orig_sess is None:
            return False
        new_sessions = []
        for s in week.sessions:
            if s.day_of_week == orig_dow:
                # zone unchanged for reduce_50 (see propose_session_adjustment) — no
                # remap needed. Duration scales by 0.65 there (propose_session_adjustment's
                # new_duration), not by 0.50 — TSS and duration are deliberately scaled by
                # different factors, so steps must follow duration's 0.65 to stay
                # consistent with session_after["duration_minutes"].
                new_steps = _scale_steps(s.steps, 0.65)
                new_sessions.append(SessionSpec(
                    day_of_week=s.day_of_week,
                    workout_type=session_after["workout_type"],
                    zone_code=session_after["zone"],
                    duration_minutes=(
                        derive_duration_minutes(new_steps) if new_steps is not None
                        else session_after["duration_minutes"]
                    ),
                    target_time_in_zone_minutes=(
                        derive_target_time_in_zone_minutes(new_steps) if new_steps is not None
                        else int(s.target_time_in_zone_minutes * 0.50)
                    ),
                    tss_target=session_after["tss_target"],
                    description_fr=session_after["description"],
                    steps=new_steps,
                ))
            else:
                new_sessions.append(s)
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=week.is_recovery_week,
            total_tss_target=round(sum(s.tss_target for s in new_sessions), 1),
            sessions=new_sessions,
            start_date=week.start_date,
        )

    elif action == "indoor" and session_after:
        new_sessions = []
        for s in week.sessions:
            if s.day_of_week == orig_dow:
                # Only the description changes — same charge, same structure.
                new_sessions.append(SessionSpec(
                    day_of_week=s.day_of_week,
                    workout_type=s.workout_type,
                    zone_code=s.zone_code,
                    duration_minutes=s.duration_minutes,
                    target_time_in_zone_minutes=s.target_time_in_zone_minutes,
                    tss_target=s.tss_target,
                    description_fr=session_after["description"],
                    steps=s.steps,
                ))
            else:
                new_sessions.append(s)
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=week.is_recovery_week,
            total_tss_target=week.total_tss_target,
            sessions=new_sessions,
            start_date=week.start_date,
        )

    elif action == "shift" and session_after:
        orig_sess = next((s for s in week.sessions if s.day_of_week == orig_dow), None)
        if orig_sess is None:
            return False

        new_day = session_after["day"]
        new_date = date.fromisoformat(session_after["date"])
        new_week_num = (new_date - plan.start_date).days // 7 + 1

        # Only the day changes — same structure, unmodified.
        shifted_sess = SessionSpec(
            day_of_week=new_day,
            workout_type=orig_sess.workout_type,
            zone_code=orig_sess.zone_code,
            duration_minutes=orig_sess.duration_minutes,
            target_time_in_zone_minutes=orig_sess.target_time_in_zone_minutes,
            tss_target=orig_sess.tss_target,
            description_fr=orig_sess.description_fr,
            steps=orig_sess.steps,
        )

        # Supprimer du jour d'origine
        new_sessions_orig = [s for s in week.sessions if s.day_of_week != orig_dow]
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=week.is_recovery_week,
            total_tss_target=round(sum(s.tss_target for s in new_sessions_orig), 1),
            sessions=new_sessions_orig,
            start_date=week.start_date,
        )

        # Ajouter au nouveau jour (même semaine ou semaine suivante)
        target_week_idx = next(
            (i for i, w in enumerate(schema.weeks) if w.week_number == new_week_num), None
        )
        if target_week_idx is None:
            return False
        target_week = schema.weeks[target_week_idx]
        new_sessions_target = sorted(
            list(target_week.sessions) + [shifted_sess], key=lambda s: s.day_of_week
        )
        schema.weeks[target_week_idx] = WeekPlan(
            week_number=target_week.week_number,
            phase=target_week.phase,
            is_recovery_week=target_week.is_recovery_week,
            total_tss_target=round(sum(s.tss_target for s in new_sessions_target), 1),
            sessions=new_sessions_target,
            start_date=target_week.start_date,
        )

    else:
        return False

    plan.plan_technical = schema.model_dump(mode="json")
    return True


def _apply_progressive_recovery(schema: TrainingPlanSchema, base_week_num: int) -> None:
    """Applique une reprise progressive sur les 2 semaines suivant la modification."""
    for offset, factor in [(1, 0.80), (2, 0.90)]:
        target = base_week_num + offset
        week = next((w for w in schema.weeks if w.week_number == target), None)
        if week is None:
            continue

        new_sessions = []
        for sess in week.sessions:
            orig_zone = sess.zone_code
            new_zone = _downgrade_zone(orig_zone) if factor < 0.85 else orig_zone
            dur_comp = _downgrade_duration_factor(orig_zone) if new_zone != orig_zone else 1.0
            effective_factor = factor * dur_comp
            zone_remap = {orig_zone: new_zone} if new_zone != orig_zone else None
            new_steps = _scale_steps(sess.steps, effective_factor, zone_remap)
            new_sessions.append(SessionSpec(
                day_of_week=sess.day_of_week,
                workout_type=sess.workout_type,
                zone_code=new_zone,
                duration_minutes=(
                    derive_duration_minutes(new_steps) if new_steps is not None
                    else max(20, int(sess.duration_minutes * effective_factor))
                ),
                target_time_in_zone_minutes=(
                    derive_target_time_in_zone_minutes(new_steps) if new_steps is not None
                    else int(sess.target_time_in_zone_minutes * factor)
                ),
                tss_target=round(sess.tss_target * factor, 1),
                description_fr=f"{sess.description_fr} [reprise S+{offset}]",
                steps=new_steps,
            ))

        week_idx = next(i for i, w in enumerate(schema.weeks) if w.week_number == target)
        schema.weeks[week_idx] = WeekPlan(
            week_number=week.week_number,
            phase=week.phase,
            is_recovery_week=False,
            total_tss_target=round(sum(s.tss_target for s in new_sessions), 1),
            sessions=new_sessions,
            start_date=week.start_date,
        )


def _downgrade_zone(zone_code: str) -> str:
    """Réduit l'intensité d'une zone d'un cran (Z6→Z5→Z4→...→Z1)."""
    if zone_code not in _ZONE_ORDER:
        return zone_code
    idx = _ZONE_ORDER.index(zone_code)
    return _ZONE_ORDER[max(0, idx - 1)]


# Compensation de durée lors d'un downgrade de zone :
# baisser l'intensité d'un cran réduit le stimulus — on compense par +15% de durée.
_ZONE_DOWNGRADE_DURATION_FACTOR = 1.15


def _downgrade_duration_factor(zone_code: str) -> float:
    """
    Retourne le facteur de compensation de durée (1.15) quand la zone est dégradée.
    Retourne 1.0 si la zone ne peut pas être réduite davantage (déjà Z1).
    """
    if zone_code not in _ZONE_ORDER:
        return 1.0
    if _ZONE_ORDER.index(zone_code) == 0:
        return 1.0  # Z1 = déjà au minimum, pas de compensation
    return _ZONE_DOWNGRADE_DURATION_FACTOR


def _build_modification_summary(modification_type: str, week_offset: int) -> str:
    week_label = "cette semaine" if week_offset == 0 else f"dans {week_offset} semaine(s)"
    summaries = {
        "reduce_intensity": f"Réduire l'intensité des séances {week_label} (−30% TSS, zones réduites d'un cran)",
        "reduce_volume": f"Réduire le volume {week_label} (−40% TSS, durées raccourcies)",
        "skip_session": f"Supprimer les séances intensives {week_label} (garder uniquement Z1/Z2)",
        "swap_to_recovery": f"Passer {week_label} en semaine de récupération complète (tout en Z2, −50% TSS)",
    }
    return summaries.get(modification_type, f"Ajustement du plan {week_label}")
