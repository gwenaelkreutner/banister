"""Pure, deterministic evaluation of training-guardrail signals (spec 006 FR-022,
SC-010).

Every function here is a pure function of its arguments: no DB, no clock, no LLM, no
randomness. `app/services/guardrail_service.py` assembles the athlete's data and calls
these; the LLM narrates the `GuardrailFinding`s it is given and produces none itself
(Constitution Principle I, FR-022).

Thresholds come from `app/engine/guardrail_thresholds.py` — nothing here hard-codes a
number (FR-016).

Populated per user story: workload evaluators (US1 = T014-T017), recovery evaluators
(US2 = T023-T024), the outlier / persistence guard (US5 = T047).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.engine.guardrail_thresholds import (
    ACWR_MIN_CTL,
    ACWR_SAFE_HIGH,
    ACWR_SAFE_LOW,
    HRV_DROP_PCT,
    MONOTONY_HIGH,
    RAMP_RATE_CAUTION,
    RAMP_RATE_HIGH,
    RHR_RISE_BPM,
)

# Severity is ordering only — so simultaneous findings can be ranked rather than dumped
# (data-model.md §Guardrail finding). Not a score, not shown to the athlete.
SEVERITY_LOW = 1
SEVERITY_MEDIUM = 2
SEVERITY_HIGH = 3


@dataclass(frozen=True)
class GuardrailFinding:
    """What one guardrail evaluation produces. Never persisted — carried in memory to the
    narration layer (data-model.md §Guardrail finding).

    `action` is a required field with no default: SC-003 requires *every* finding to
    carry a recommended action, and a type that cannot represent an actionless finding
    enforces that at construction rather than at review.
    """

    kind: str
    observed: str          # the value measured, formatted for display
    reference: str          # the baseline or range it was measured against
    threshold: str          # the documented value that was crossed
    action: str             # what the athlete can do — MANDATORY, non-empty (FR-027)
    severity: int           # SEVERITY_* — ordering only
    occurrence_key: str     # stable identity for "this same occurrence" (FR-025)

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError(
                "a GuardrailFinding must carry an action — a finding that says a number "
                "is bad without saying what to do is the failure FR-027 names"
            )
        if not self.observed.strip() or not self.reference.strip():
            raise ValueError(
                "a GuardrailFinding must state its observed value and its reference "
                "point — a bare number is meaningless (SC-003)"
            )


def _occurrence_key(kind: str, finding_date: date) -> str:
    """`kind:yyyy-mm-dd` — the day the finding is *about*, so declining today's finding
    settles today and tomorrow's evaluation is genuinely new (data-model.md). Fully
    fleshed out in US4/T040; the shape is fixed here."""
    return f"{kind}:{finding_date.isoformat()}"


# ── Workload signals (US1) ───────────────────────────────────────────────────


def evaluate_acwr(
    atl: float | None, ctl: float | None, *, finding_date: date
) -> GuardrailFinding | None:
    """The acute:chronic workload ratio, `ATL / CTL` read from the source (research R3).

    Fires only when the ratio is **above** the documented range — the US1 case, "warned
    before digging the hole". A ratio below range is not raised as a warning: a planned
    taper produces exactly that and must not be read as detraining (the spec's own edge
    case); sustained under-load shows up in the ramp rate instead.

    Returns `None` when CTL is below `ACWR_MIN_CTL` — on a thin chronic base the ratio
    reflects the base, not a real spike (the messaging for that lands in US5/T048).
    """
    if atl is None or ctl is None or ctl <= 0:
        return None
    if ctl < ACWR_MIN_CTL:
        return None
    ratio = atl / ctl
    if ratio <= ACWR_SAFE_HIGH:
        return None  # within (or below) range — no manufactured warning (FR-005)
    return GuardrailFinding(
        kind="acwr_high",
        observed=f"{ratio:.2f}",
        reference=f"{ACWR_SAFE_LOW:.2f} – {ACWR_SAFE_HIGH:.2f}",
        threshold=f"{ACWR_SAFE_HIGH:.2f}",
        action=(
            "réduis la charge des prochains jours plutôt que de l'augmenter — garde une "
            "séance de qualité, remplace les autres par du Z2 court, jusqu'à ce que le "
            "rapport redescende dans la plage"
        ),
        severity=SEVERITY_HIGH,
        occurrence_key=_occurrence_key("acwr_high", finding_date),
    )


def evaluate_ramp_rate(
    ramp_rate: float | None, *, finding_date: date
) -> GuardrailFinding | None:
    """CTL gain per week, consumed as-is from the source (research R4). A second,
    independent workload signal: the ratio asks "is today's load out of proportion", the
    ramp asks "how fast is the absorbed level itself moving"."""
    if ramp_rate is None:
        return None
    if ramp_rate >= RAMP_RATE_HIGH:
        return GuardrailFinding(
            kind="ramp_rate_high",
            observed=f"{ramp_rate:.1f} pts/sem",
            reference=f"{RAMP_RATE_CAUTION:.0f}–{RAMP_RATE_HIGH:.0f} pts/sem = prudence",
            threshold=f"{RAMP_RATE_HIGH:.0f} pts/sem",
            action=(
                "ta forme de fond monte trop vite pour être tenable — stabilise le "
                "volume hebdo pendant 7 à 10 jours avant de repartir à la hausse"
            ),
            severity=SEVERITY_HIGH,
            occurrence_key=_occurrence_key("ramp_rate_high", finding_date),
        )
    if ramp_rate >= RAMP_RATE_CAUTION:
        return GuardrailFinding(
            kind="ramp_rate_caution",
            observed=f"{ramp_rate:.1f} pts/sem",
            reference=f"{RAMP_RATE_CAUTION:.0f}–{RAMP_RATE_HIGH:.0f} pts/sem = prudence",
            threshold=f"{RAMP_RATE_CAUTION:.0f} pts/sem",
            action=(
                "tu es en haut de la bande de progression soutenable — n'ajoute pas de "
                "charge cette semaine, tiens le niveau actuel"
            ),
            severity=SEVERITY_MEDIUM,
            occurrence_key=_occurrence_key("ramp_rate_caution", finding_date),
        )
    return None


def evaluate_monotony(
    monotony_index: float | None, *, finding_date: date
) -> GuardrailFinding | None:
    """Foster monotony — insufficient day-to-day load variation is a risk in its own
    right (FR-004). Uses the corrected value from `weekly_snapshot.py` (research R2)."""
    if monotony_index is None:
        return None
    if monotony_index <= MONOTONY_HIGH:
        return None
    return GuardrailFinding(
        kind="monotony_high",
        observed=f"{monotony_index:.1f}",
        reference=f"> {MONOTONY_HIGH:.1f} = charge trop uniforme",
        threshold=f"{MONOTONY_HIGH:.1f}",
        action=(
            "ta semaine manque de contraste — intercale un vrai jour facile (ou de "
            "repos) et concentre l'intensité sur moins de séances, plus dures"
        ),
        severity=SEVERITY_MEDIUM,
        occurrence_key=_occurrence_key("monotony_high", finding_date),
    )


# ── Recovery signals (US2) ───────────────────────────────────────────────────
#
# Only HRV and resting HR have documented thresholds (FR-016). Sleep is captured but no
# published, citable threshold applies to it, so no evaluator fires on it alone — adding
# an unexplained one would fail FR-016. A `None` observation or a `None` baseline always
# yields `None`: missing is unknown, never a default (FR-013, FR-014).

RECOVERY_KINDS = frozenset({"hrv_low", "rhr_high", "recovery_multi"})


def evaluate_hrv(
    observed: float | None, baseline: float | None, *, finding_date: date
) -> GuardrailFinding | None:
    """HRV more than `HRV_DROP_PCT` below the athlete's own baseline directs an easy day
    (FR-007). HRV falls when recovery is incomplete, so the trigger is `observed` well
    *below* `baseline`."""
    if observed is None or baseline is None or baseline <= 0:
        return None
    drop_pct = (observed - baseline) / baseline * 100
    if drop_pct > HRV_DROP_PCT:
        return None
    return GuardrailFinding(
        kind="hrv_low",
        observed=f"VFC {observed:.0f} ({drop_pct:+.0f}% vs ta normale)",
        reference=f"ta normale : {baseline:.0f}",
        threshold=f"{HRV_DROP_PCT:.0f}%",
        action=(
            "ta variabilité cardiaque est nettement sous ta normale — fais une vraie "
            "journée facile aujourd'hui (Z1-Z2 court ou repos), pas d'intensité"
        ),
        severity=SEVERITY_HIGH,
        occurrence_key=_occurrence_key("hrv_low", finding_date),
    )


def evaluate_resting_hr(
    observed: float | None, baseline: float | None, *, finding_date: date
) -> GuardrailFinding | None:
    """Resting HR `RHR_RISE_BPM` or more above the athlete's own baseline raises a
    fatigue signal (FR-008)."""
    if observed is None or baseline is None:
        return None
    rise = observed - baseline
    if rise < RHR_RISE_BPM:
        return None
    return GuardrailFinding(
        kind="rhr_high",
        observed=f"FC repos {observed:.0f} bpm (+{rise:.0f} vs ta normale)",
        reference=f"ta normale : {baseline:.0f} bpm",
        threshold=f"+{RHR_RISE_BPM} bpm",
        action=(
            "ta fréquence cardiaque de repos est au-dessus de ta normale — signe de "
            "fatigue ou de début d'infection : allège la journée et surveille demain"
        ),
        severity=SEVERITY_MEDIUM,
        occurrence_key=_occurrence_key("rhr_high", finding_date),
    )


def combine_recovery_findings(findings: list[GuardrailFinding]) -> list[GuardrailFinding]:
    """When two or more recovery signals are poor at once, replace them with a single
    finding of higher severity — the combination is more significant than any one alone
    (FR-009). Non-recovery findings pass through untouched."""
    recovery = [f for f in findings if f.kind in RECOVERY_KINDS]
    if len(recovery) < 2:
        return findings
    others = [f for f in findings if f.kind not in RECOVERY_KINDS]
    day = recovery[0].occurrence_key.split(":", 1)[1]
    combined = GuardrailFinding(
        kind="recovery_multi",
        observed=" ; ".join(f.observed for f in recovery),
        reference=" ; ".join(f.reference for f in recovery),
        threshold="plusieurs seuils franchis en même temps",
        action=(
            "plusieurs signaux de récupération sont bas simultanément — c'est plus "
            "significatif que chacun pris isolément. Journée vraiment facile ou repos "
            "complet aujourd'hui, et réévalue demain matin avant de reprendre"
        ),
        severity=SEVERITY_HIGH,
        occurrence_key=f"recovery_multi:{day}",
    )
    return [*others, combined]


def as_signal_only(finding: GuardrailFinding) -> GuardrailFinding:
    """The athlete has declined acting on this occurrence. The signal keeps appearing
    (FR-026) but its action is demoted from a recommendation to a restatement — the coach
    does not push the same change again (FR-025)."""
    return GuardrailFinding(
        kind=finding.kind,
        observed=finding.observed,
        reference=finding.reference,
        threshold=finding.threshold,
        action=(
            "(l'athlète a déjà choisi de ne pas ajuster pour ce signal — mentionne-le "
            "factuellement s'il en reparle, sans reproposer de changement ni insister)"
        ),
        severity=finding.severity,
        occurrence_key=finding.occurrence_key,
    )


def state_conflict_with_plan(
    finding: GuardrailFinding, prescribed_workout_type: str, prescribed_zone: str
) -> GuardrailFinding:
    """When a recovery finding lands on a day the plan prescribes a hard session, the
    finding's action must name the conflict openly rather than let it be resolved
    silently (FR-012). Returns a new finding with the conflict appended to its action."""
    return GuardrailFinding(
        kind=finding.kind,
        observed=finding.observed,
        reference=finding.reference,
        threshold=finding.threshold,
        action=(
            f"{finding.action}. ⚠️ Le plan prévoit une séance dure aujourd'hui "
            f"({prescribed_workout_type} {prescribed_zone}) — dis-le clairement à "
            f"l'athlète et propose l'échange, ne tranche pas à sa place"
        ),
        severity=finding.severity,
        occurrence_key=finding.occurrence_key,
    )
