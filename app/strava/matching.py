from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.engine.schemas import SessionSpec, TrainingPlanSchema
from app.strava.analysis_models import AnalyzedSession

# ── Paires plan / réalisé ─────────────────────────────────────────────────────


@dataclass(slots=True)
class SessionPair:
    """Association entre une séance planifiée et l'activité réalisée (ou non)."""
    planned_date: date
    day_of_week: int
    session_spec: SessionSpec | None  # None = activité bonus non planifiée
    session_log: object | None        # SessionLog duck-typed | None = séance manquée


def build_activity_session_pairs(
    plan_schema: TrainingPlanSchema,
    plan_start_date: date,
    session_logs: list,
    week_number: int,
) -> list[SessionPair]:
    """Construit les paires (séance planifiée ↔ activité réalisée) pour une semaine.

    Retourne une liste triée par date incluant :
    - Séances réalisées  : (SessionSpec, SessionLog)
    - Séances manquées   : (SessionSpec, None)        ← passé aujourd'hui
    - Activités bonus    : (None, SessionLog)          ← hors plan
    """
    current_week = next(
        (w for w in plan_schema.weeks if w.week_number == week_number), None
    )

    # Index des logs "done" par day_of_week pour cette semaine
    done_logs: dict[int, object] = {}
    unplanned_logs: list[object] = []

    for log in session_logs:
        if getattr(log, "week_number", None) != week_number:
            continue
        status = getattr(log, "status", "")
        dow = getattr(log, "day_of_week", None)
        if status == "done" and dow is not None:
            done_logs[dow] = log
        elif status == "unplanned":
            unplanned_logs.append(log)

    pairs: list[SessionPair] = []

    # 1. Séances planifiées (réalisées ou manquées)
    if current_week:
        for sess in current_week.sessions:
            planned_date = plan_start_date + timedelta(
                days=(week_number - 1) * 7 + sess.day_of_week
            )
            pairs.append(SessionPair(
                planned_date=planned_date,
                day_of_week=sess.day_of_week,
                session_spec=sess,
                session_log=done_logs.get(sess.day_of_week),
            ))

    # 2. Activités bonus (non planifiées dans cette semaine)
    for log in unplanned_logs:
        log_date = getattr(log, "logged_date", plan_start_date)
        log_dow = getattr(log, "day_of_week", 0)
        pairs.append(SessionPair(
            planned_date=log_date,
            day_of_week=log_dow,
            session_spec=None,
            session_log=log,
        ))

    pairs.sort(key=lambda p: p.planned_date)

    # Supprimer les activités bonus sur un jour RÉELLEMENT déjà occupé par une séance matchée.
    # On compare la date réelle du log (logged_date) et non la date planifiée,
    # afin de conserver les bonus qui tombent un jour différent même si le slot
    # planifié est le même (ex : longue faite le sam → slot dim, bonus fait le dim → gardé).
    matched_actual_dates = {
        getattr(p.session_log, "logged_date", p.planned_date)
        for p in pairs
        if p.session_spec is not None and p.session_log is not None
    }
    pairs = [
        p for p in pairs
        if not (p.session_spec is None and p.planned_date in matched_actual_dates)
    ]

    return pairs

_MATCH_MIN_CONFIDENCE = 50
_TOLERANCE_DAYS = 2

# Matrice de compatibilité session_type_real × workout_type planifié (0-25 pts)
# Lignes : session_type_real (réalisé) | Colonnes : workout_type (planifié)
_TYPE_COMPAT: dict[str, dict[str, int]] = {
    "long_ride":  {"long_ride": 25, "endurance": 15, "intervals":  5, "recovery":  0},
    "endurance":  {"long_ride": 15, "endurance": 25, "intervals":  5, "recovery": 10},
    "tempo":      {"long_ride": 10, "endurance": 15, "intervals": 15, "recovery":  0},
    "intervals":  {"long_ride":  5, "endurance": 10, "intervals": 25, "recovery":  0},
    "recovery":   {"long_ride":  0, "endurance": 10, "intervals":  0, "recovery": 25},
    "race":       {"long_ride": 10, "endurance":  5, "intervals": 10, "recovery":  0},
    "unknown":    {"long_ride": 12, "endurance": 12, "intervals": 12, "recovery": 12},
}


@dataclass(slots=True)
class SessionCandidate:
    week_number: int
    day_of_week: int
    session_spec: SessionSpec
    day_shift: int


@dataclass(slots=True)
class MatchScoreResult:
    match_level: str
    confidence_score: int
    reasons: list[str]


@dataclass(slots=True)
class ActivitySessionMatch:
    candidate: SessionCandidate | None
    score: MatchScoreResult | None
    # True si des candidats existent dans la fenêtre mais tous déjà pris par d'autres activités
    all_slots_taken: bool = field(default=False)

    @property
    def is_aligned(self) -> bool:
        return self.score is not None and self.score.confidence_score >= _MATCH_MIN_CONFIDENCE


def find_plan_candidate(
    plan,
    activity_date: date,
    analyzed: AnalyzedSession,
    tolerance_days: int = _TOLERANCE_DAYS,
    used_slots: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[SessionCandidate | None, bool]:
    """Collecte tous les candidats dans la fenêtre temporelle (même semaine d'entraînement),
    les score sémantiquement, retourne (meilleur_candidat, all_slots_taken).

    all_slots_taken=True : des candidats existent dans la fenêtre mais tous déjà pris.
    """
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)

    # Semaine d'entraînement de l'activité (0-indexée, relative à plan.start_date)
    activity_week_idx = max(0, (activity_date - plan.start_date).days // 7)

    best_candidate: SessionCandidate | None = None
    best_score: int = -1
    has_any_candidate = False  # candidat dans la fenêtre, slot pris ou non

    for week in schema.weeks:
        week_idx = week.week_number - 1
        if week_idx != activity_week_idx:
            continue  # Uniquement la même semaine d'entraînement

        for sess in week.sessions:
            planned_date = plan.start_date + timedelta(days=(week_idx * 7 + sess.day_of_week))
            day_shift = (activity_date - planned_date).days
            if abs(day_shift) > tolerance_days:
                continue

            has_any_candidate = True

            # Ignorer les slots déjà pris par une autre activité
            if (week.week_number, sess.day_of_week) in used_slots:
                continue

            cand = SessionCandidate(
                week_number=week.week_number,
                day_of_week=sess.day_of_week,
                session_spec=sess,
                day_shift=day_shift,
            )
            score = score_activity_vs_session(analyzed, sess)
            # Pénalité temporelle : -2 pts par jour de décalage
            adjusted_score = score.confidence_score - abs(day_shift) * 2
            if adjusted_score > best_score or (
                adjusted_score == best_score
                and best_candidate is not None
                and abs(day_shift) < abs(best_candidate.day_shift)
            ):
                best_score = adjusted_score
                best_candidate = cand

    all_slots_taken = has_any_candidate and best_candidate is None
    return best_candidate, all_slots_taken


def score_activity_vs_session(
    analyzed: AnalyzedSession,
    session_spec: SessionSpec,
) -> MatchScoreResult:
    """Compare l'activité réalisée avec la séance planifiée.

    Score sur 100 pts répartis en 4 dimensions :
      - Durée        : 30 pts
      - TSS/charge   : 30 pts
      - Type séance  : 25 pts (neutre à 12 si session_type_real == 'unknown')
      - Zone dominante: 5 pts (bonus)
      - Contexte     : 10 pts (indoor/outdoor)
    """
    reasons: list[str] = []
    total = 0

    # ── 1. Durée (30 pts) ─────────────────────────────────────────────────────
    elapsed_minutes = max(0, int(analyzed.duration_s / 60))
    planned_minutes = max(1, int(session_spec.duration_minutes))
    duration_ratio = elapsed_minutes / planned_minutes

    dur_score = _score_duration(duration_ratio, session_spec.workout_type)
    total += dur_score
    if 0.85 <= duration_ratio <= 1.15:
        reasons.append(f"Durée cohérente ({elapsed_minutes} min vs {planned_minutes} min prévues)")
    else:
        reasons.append(f"Durée éloignée ({elapsed_minutes} min vs {planned_minutes} min prévues)")

    # ── 2. TSS/charge (30 pts) ────────────────────────────────────────────────
    # analyzed.tss can be None — the source has no computed load for ~15% of real
    # activities (spec 002 research R9c). Neutral score, same treatment as an
    # unrecognized session_type_real below, rather than crashing on None / float.
    tss_target = max(1.0, float(session_spec.tss_target))
    if analyzed.tss is None:
        load_score = 15  # neutral midpoint of the 30-pt range
        reasons.append("Charge inconnue (pas de TSS disponible pour cette activité)")
    else:
        tss_ratio = analyzed.tss / tss_target
        load_score = _score_load(tss_ratio)
        margin = 0.30
        if (1 - margin) <= tss_ratio <= (1 + margin):
            reasons.append(
                f"Charge cohérente ({analyzed.tss:.0f} vs TSS cible {round(tss_target)})"
            )
        else:
            reasons.append(
                f"Charge éloignée ({analyzed.tss:.0f} vs TSS cible {round(tss_target)})"
            )
    total += load_score

    # ── 3. Type de séance (25 pts) ────────────────────────────────────────────
    session_type = analyzed.session_type_real or "unknown"
    type_score = _TYPE_COMPAT.get(session_type, _TYPE_COMPAT["unknown"]).get(
        session_spec.workout_type, 12
    )
    total += type_score
    if session_type != "unknown":
        if type_score >= 20:
            reasons.append(f"Type cohérent ({session_type} → {session_spec.workout_type})")
        elif type_score >= 10:
            reasons.append(f"Type compatible ({session_type} → {session_spec.workout_type})")
        else:
            reasons.append(f"Type incompatible ({session_type} ≠ {session_spec.workout_type})")
    else:
        reasons.append("Type d'effort indéterminé (score neutre)")

    # ── 4. Zone dominante (5 pts bonus) ───────────────────────────────────────
    if analyzed.dominant_zone and analyzed.dominant_zone == session_spec.zone_code:
        total += 5
        reasons.append(f"Zone dominante alignée ({analyzed.dominant_zone})")

    # ── 5. Contexte indoor/outdoor (10 pts) ───────────────────────────────────
    ctx_score, ctx_reason = _score_context(analyzed.environment, session_spec.workout_type)
    total += ctx_score
    reasons.append(ctx_reason)

    confidence = max(0, min(100, int(total)))
    match_level = _match_level_from_confidence(confidence)
    return MatchScoreResult(match_level=match_level, confidence_score=confidence, reasons=reasons)


def evaluate_activity_plan_match(
    plan,
    analyzed: AnalyzedSession,
    activity_date: date,
    tolerance_days: int = _TOLERANCE_DAYS,
    used_slots: frozenset[tuple[int, int]] = frozenset(),
) -> ActivitySessionMatch:
    candidate, all_slots_taken = find_plan_candidate(
        plan, activity_date, analyzed,
        tolerance_days=tolerance_days,
        used_slots=used_slots,
    )
    if candidate is None:
        return ActivitySessionMatch(candidate=None, score=None, all_slots_taken=all_slots_taken)
    score = score_activity_vs_session(analyzed, candidate.session_spec)
    return ActivitySessionMatch(candidate=candidate, score=score)


# ── Fonctions internes ────────────────────────────────────────────────────────

def _score_duration(duration_ratio: float, workout_type: str) -> int:
    """Score durée sur 30 pts. Décroissance linéaire au-delà de ±10%, zéro à ±75%."""
    if 0.9 <= duration_ratio <= 1.1:
        return 30
    diff = abs(duration_ratio - 1.0)
    return int(max(0, 30 - diff * 40))


def _score_load(tss_ratio: float) -> int:
    """Score charge TSS sur 30 pts."""
    diff = abs(tss_ratio - 1.0)
    return int(max(0, 30 - diff * 40))


def _score_context(environment: str | None, workout_type: str) -> tuple[int, str]:
    is_indoor = environment == "indoor"

    if workout_type in {"intervals", "recovery"}:
        if is_indoor:
            return 10, "Contexte cohérent : séance structurée en indoor"
        return 7, "Contexte acceptable : séance structurée en outdoor"

    if workout_type == "long_ride":
        if is_indoor:
            return 3, "Contexte peu aligné : sortie longue en indoor"
        return 10, "Contexte cohérent : sortie longue en outdoor"

    # endurance ou inconnu
    if environment is None:
        return 7, "Contexte non déterminé"
    return 8, "Contexte neutre"


def _match_level_from_confidence(confidence: int) -> str:
    if confidence >= 85:
        return "exact"
    if confidence >= 65:
        return "close"
    if confidence >= 45:
        return "weak"
    return "none"
