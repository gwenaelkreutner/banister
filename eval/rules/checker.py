"""Vérificateur de règles déterministes pour les plans d'entraînement générés.

Chaque règle retourne une liste de RuleViolation. Le module expose check_all()
qui agrège l'ensemble des vérifications.

Règles implémentées (14) :
  Safety    : consecutive_intensity, tss_spike_week, no_recovery_week,
               z5_too_early_beginner, z4_week1
  Structure : invalid_final_phase, session_out_of_bounds, session_wrong_day,
               zero_tss_session, interval_time_in_zone_zero, weekly_volume_exceeded
  Progression: peak_tss_not_increasing, taper_not_lighter, recovery_week_too_high
"""

from dataclasses import dataclass, field
from typing import Literal

from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema

# Mapping jour textuel → entier (0=Lundi, 6=Dimanche)
_DAY_TO_INT: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_INT_TO_DAY: dict[int, str] = {v: k for k, v in _DAY_TO_INT.items()}

_HIGH_INTENSITY_ZONES = {"Z4", "Z5", "Z6"}


@dataclass
class RuleViolation:
    rule_id: str
    severity: Literal["critical", "warning"]
    week: int | None  # None = violation au niveau du plan entier
    description: str


@dataclass
class CheckResult:
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "violations": [
                {
                    "rule_id": v.rule_id,
                    "severity": v.severity,
                    "week": v.week,
                    "description": v.description,
                }
                for v in self.violations
            ],
        }


def check_all(profile: AthleteProfileSchema, plan: TrainingPlanSchema) -> CheckResult:
    """Lance l'ensemble des 14 règles et retourne un CheckResult agrégé."""
    violations: list[RuleViolation] = []
    violations.extend(_check_consecutive_intensity(plan))
    violations.extend(_check_tss_spike(plan))
    violations.extend(_check_no_recovery_week(plan))
    violations.extend(_check_z5_too_early_beginner(profile, plan))
    violations.extend(_check_z4_week1(plan))
    violations.extend(_check_invalid_final_phase(profile, plan))
    violations.extend(_check_session_bounds(plan))
    violations.extend(_check_session_wrong_day(profile, plan))
    violations.extend(_check_zero_tss(plan))
    violations.extend(_check_interval_zone_time(plan))
    violations.extend(_check_weekly_volume(profile, plan))
    violations.extend(_check_peak_tss(plan))
    violations.extend(_check_taper_lighter(plan))
    violations.extend(_check_recovery_week_tss(plan))
    return CheckResult(violations=violations)


# ── Règles Safety ─────────────────────────────────────────────────────────────

def _check_consecutive_intensity(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Z4+ deux jours consécutifs sans gap ≥ 2 jours (intra et inter-semaine)."""
    violations: list[RuleViolation] = []

    for week in plan.weeks:
        hi = sorted(
            [s for s in week.sessions if s.zone_code in _HIGH_INTENSITY_ZONES],
            key=lambda s: s.day_of_week,
        )
        for i in range(len(hi) - 1):
            gap = hi[i + 1].day_of_week - hi[i].day_of_week
            if gap < 2:
                violations.append(RuleViolation(
                    rule_id="consecutive_intensity",
                    severity="critical",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séances {hi[i].zone_code} et "
                        f"{hi[i+1].zone_code} séparées de seulement {gap} jour(s) "
                        f"(jours {hi[i].day_of_week} et {hi[i+1].day_of_week})"
                    ),
                ))

    # Vérification inter-semaine
    for i in range(len(plan.weeks) - 1):
        curr_week = plan.weeks[i]
        next_week = plan.weeks[i + 1]
        curr_hi = [s for s in curr_week.sessions if s.zone_code in _HIGH_INTENSITY_ZONES]
        next_hi = [s for s in next_week.sessions if s.zone_code in _HIGH_INTENSITY_ZONES]
        if not curr_hi or not next_hi:
            continue
        last_day = max(s.day_of_week for s in curr_hi)
        first_day_next = min(s.day_of_week for s in next_hi)
        # Nombre de jours entre le dernier jour de la semaine N et le premier de N+1
        gap = (7 - last_day) + first_day_next
        if gap < 2:
            violations.append(RuleViolation(
                rule_id="consecutive_intensity",
                severity="critical",
                week=next_week.week_number,
                description=(
                    f"Transition S{curr_week.week_number}→S{next_week.week_number} : "
                    f"séances haute intensité séparées de seulement {gap} jour(s)"
                ),
            ))

    return violations


def _check_tss_spike(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Augmentation TSS hebdomadaire > 10% hors semaine de récupération."""
    violations: list[RuleViolation] = []
    for i in range(1, len(plan.weeks)):
        prev = plan.weeks[i - 1]
        curr = plan.weeks[i]
        if curr.is_recovery_week or prev.is_recovery_week:
            continue
        if prev.total_tss_target <= 0:
            continue
        pct = (curr.total_tss_target - prev.total_tss_target) / prev.total_tss_target * 100
        if pct > 10.0:
            violations.append(RuleViolation(
                rule_id="tss_spike_week",
                severity="critical",
                week=curr.week_number,
                description=(
                    f"S{curr.week_number} : hausse TSS de {pct:.1f}% "
                    f"({prev.total_tss_target:.0f} → {curr.total_tss_target:.0f}) — "
                    "maximum recommandé : 10%"
                ),
            ))
    return violations


def _check_no_recovery_week(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Bloc de 4 semaines consécutives ou plus sans semaine de récupération."""
    violations: list[RuleViolation] = []
    block_start: int | None = None
    consecutive = 0

    for week in plan.weeks:
        if week.is_recovery_week:
            if consecutive >= 4:
                violations.append(RuleViolation(
                    rule_id="no_recovery_week",
                    severity="warning",
                    week=week.week_number - 1,
                    description=(
                        f"Bloc de {consecutive} semaines consécutives sans récupération "
                        f"(S{block_start}–S{week.week_number - 1})"
                    ),
                ))
            consecutive = 0
            block_start = None
        else:
            if block_start is None:
                block_start = week.week_number
            consecutive += 1
            if consecutive == 4:
                # Reporter la violation dès la 4e semaine, pas à la récup suivante
                violations.append(RuleViolation(
                    rule_id="no_recovery_week",
                    severity="warning",
                    week=week.week_number,
                    description=(
                        f"4 semaines consécutives sans récupération "
                        f"(S{block_start}–S{week.week_number})"
                    ),
                ))

    return violations


def _check_z5_too_early_beginner(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> list[RuleViolation]:
    """Z5+ pour un débutant avant la 4e semaine."""
    if profile.level != "beginner":
        return []
    violations: list[RuleViolation] = []
    for week in plan.weeks[:3]:
        for session in week.sessions:
            if session.zone_code in {"Z5", "Z6"}:
                violations.append(RuleViolation(
                    rule_id="z5_too_early_beginner",
                    severity="critical",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance {session.zone_code} pour un débutant "
                        "— adaptation physiologique insuffisante avant la semaine 4"
                    ),
                ))
    return violations


def _check_z4_week1(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Z4+ en semaine 1 quel que soit le niveau."""
    if not plan.weeks:
        return []
    violations: list[RuleViolation] = []
    for session in plan.weeks[0].sessions:
        if session.zone_code in _HIGH_INTENSITY_ZONES:
            violations.append(RuleViolation(
                rule_id="z4_week1",
                severity="warning",
                week=1,
                description=(
                    f"S1 : séance {session.zone_code} dès la première semaine — "
                    "recommandé uniquement si l'athlète est en forme et en activité continue"
                ),
            ))
    return violations


# ── Règles Structure ──────────────────────────────────────────────────────────

def _check_invalid_final_phase(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> list[RuleViolation]:
    """Dernière semaine ≠ taper alors qu'une date objectif est définie."""
    if not profile.objective.target_date or not plan.weeks:
        return []
    last_phase = plan.weeks[-1].phase
    if last_phase != "taper":
        return [RuleViolation(
            rule_id="invalid_final_phase",
            severity="warning",
            week=plan.weeks[-1].week_number,
            description=(
                f"Dernière semaine en phase '{last_phase}' (attendu : taper) "
                "alors qu'une date objectif est définie"
            ),
        )]
    return []


def _check_session_bounds(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Durée de séance en dehors de [20, 300] minutes."""
    violations: list[RuleViolation] = []
    for week in plan.weeks:
        for session in week.sessions:
            if session.duration_minutes < 20:
                violations.append(RuleViolation(
                    rule_id="session_out_of_bounds",
                    severity="warning",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance de {session.duration_minutes}min "
                        "(minimum attendu : 20min)"
                    ),
                ))
            elif session.duration_minutes > 300:
                violations.append(RuleViolation(
                    rule_id="session_out_of_bounds",
                    severity="warning",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance de {session.duration_minutes}min "
                        "(maximum attendu : 300min = 5h)"
                    ),
                ))
    return violations


def _check_session_wrong_day(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> list[RuleViolation]:
    """Séance planifiée sur un jour non disponible selon le profil."""
    allowed = {_DAY_TO_INT[d] for d in profile.availability.preferred_days}
    violations: list[RuleViolation] = []
    for week in plan.weeks:
        for session in week.sessions:
            if session.day_of_week not in allowed:
                day_name = _INT_TO_DAY.get(session.day_of_week, str(session.day_of_week))
                violations.append(RuleViolation(
                    rule_id="session_wrong_day",
                    severity="critical",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance planifiée le {day_name} "
                        f"(jours disponibles : {', '.join(profile.availability.preferred_days)})"
                    ),
                ))
    return violations


def _check_zero_tss(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Séance avec TSS cible ≤ 0."""
    violations: list[RuleViolation] = []
    for week in plan.weeks:
        for session in week.sessions:
            if session.tss_target <= 0:
                violations.append(RuleViolation(
                    rule_id="zero_tss_session",
                    severity="warning",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance {session.workout_type} "
                        f"avec TSS cible = {session.tss_target}"
                    ),
                ))
    return violations


def _check_interval_zone_time(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Séance intervals sans target_time_in_zone défini."""
    violations: list[RuleViolation] = []
    for week in plan.weeks:
        for session in week.sessions:
            if session.workout_type == "intervals" and session.target_time_in_zone_minutes == 0:
                violations.append(RuleViolation(
                    rule_id="interval_time_in_zone_zero",
                    severity="warning",
                    week=week.week_number,
                    description=(
                        f"S{week.week_number} : séance intervals ({session.zone_code}) "
                        "sans temps cible en zone renseigné"
                    ),
                ))
    return violations


def _check_weekly_volume(
    profile: AthleteProfileSchema, plan: TrainingPlanSchema
) -> list[RuleViolation]:
    """Volume total des séances dépasse le temps disponible (tolérance 15%)."""
    max_minutes = profile.availability.hours_per_week * 60
    threshold = max_minutes * 1.15
    violations: list[RuleViolation] = []
    for week in plan.weeks:
        if week.is_recovery_week:
            continue
        total = sum(s.duration_minutes for s in week.sessions)
        if total > threshold:
            violations.append(RuleViolation(
                rule_id="weekly_volume_exceeded",
                severity="warning",
                week=week.week_number,
                description=(
                    f"S{week.week_number} : volume total {total}min ({total/60:.1f}h) "
                    f"> disponibilité {max_minutes}min ({profile.availability.hours_per_week}h) "
                    f"+ tolérance 15% = {threshold:.0f}min"
                ),
            ))
    return violations


# ── Règles Progression ────────────────────────────────────────────────────────

def _check_peak_tss(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """TSS de pic ≤ TSS initial — aucune progression de charge."""
    if plan.peak_weekly_tss <= plan.initial_weekly_tss:
        return [RuleViolation(
            rule_id="peak_tss_not_increasing",
            severity="critical",
            week=None,
            description=(
                f"TSS pic ({plan.peak_weekly_tss:.0f}) ≤ TSS initial "
                f"({plan.initial_weekly_tss:.0f}) — le plan ne progresse pas"
            ),
        )]
    return []


def _check_taper_lighter(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Semaine de taper finale avec TSS ≥ la semaine précédente."""
    if len(plan.weeks) < 2:
        return []
    last = plan.weeks[-1]
    prev = plan.weeks[-2]
    if last.phase == "taper" and last.total_tss_target >= prev.total_tss_target:
        return [RuleViolation(
            rule_id="taper_not_lighter",
            severity="warning",
            week=last.week_number,
            description=(
                f"Semaine taper (S{last.week_number}) : TSS {last.total_tss_target:.0f} "
                f"≥ semaine précédente {prev.total_tss_target:.0f} — "
                "le taper ne réduit pas suffisamment la charge"
            ),
        )]
    return []


def _check_recovery_week_tss(plan: TrainingPlanSchema) -> list[RuleViolation]:
    """Semaine de récupération avec TSS > 75% de la semaine précédente."""
    violations: list[RuleViolation] = []
    for i in range(1, len(plan.weeks)):
        week = plan.weeks[i]
        prev = plan.weeks[i - 1]
        if not week.is_recovery_week or prev.total_tss_target <= 0:
            continue
        ratio = week.total_tss_target / prev.total_tss_target
        if ratio > 0.75:
            violations.append(RuleViolation(
                rule_id="recovery_week_too_high",
                severity="warning",
                week=week.week_number,
                description=(
                    f"S{week.week_number} (récupération) à {ratio * 100:.0f}% de la semaine "
                    f"précédente ({week.total_tss_target:.0f} / {prev.total_tss_target:.0f} TSS) "
                    "— recommandé < 75%"
                ),
            ))
    return violations
