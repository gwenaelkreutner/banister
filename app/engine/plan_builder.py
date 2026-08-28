"""
Orchestrateur du moteur déterministe.
Input  : AthleteProfileSchema (Pydantic)
Output : TrainingPlanSchema   (Pydantic)

Aucun I/O — pure Python, 100% testable.

Principes :
- Baseline TSS depuis le CTL de la source (CTL × 7) si disponible, sinon hours × 40
- Nombre de séances déterminé par le volume, pas par les jours disponibles
- Chaque semaine (hors récup) = long_ride + threshold + VO2 (si volume suffisant) + endurance
- Distribution pyramidale : Z1/Z2 ≈ 70-75 %, Z3/Z4 ≈ 15-20 %, Z5+ ≈ 5-8 %
- Zones progressives : Z2 → Z3 → Z4 → Z5 (jamais de saut)
- Intervalles structurés et progressifs (threshold ≠ VO2, variation semaine par semaine)
- HIGH_INTENSITY_ZONES = {Z3, Z4, Z5} — gap ≥ 2j entre séances intenses (Sweet Spot inclus)
- Max 2 séances haute intensité par semaine, jamais consécutives
- VO2max : échauffement 20 min (WARMUP_VO2_MIN) vs 15 min pour SS/threshold
"""

from datetime import date, timedelta

from app.engine.periodization import (
    compute_peak_tss,
    compute_phase_sequence,
    compute_weekly_tss_targets,
)
from app.engine.schemas import (
    AthleteProfileSchema,
    RepeatGroup,
    SessionSpec,
    Step,
    TrainingPlanSchema,
    WeekPlan,
    derive_duration_minutes,
    derive_target_time_in_zone_minutes,
    derive_zone_code,
)
from app.engine.session_library import select_template
from app.engine.tss import (
    estimate_session_tss,
    estimate_structured_session_tss,
    tss_from_weekly_hours,
)
from app.engine.zones import compute_hr_zones, compute_power_zones

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Zones considérées comme haute intensité (règle 48h)
# Z3 inclus : une séance Sweet Spot (88-93% FTP) est aussi exigeante qu'un threshold court
HIGH_INTENSITY_ZONES = {"Z3", "Z4", "Z5"}

# Variantes de séances endurance (rotation par semaine)
ENDURANCE_VARIANTS = [
    "endurance régulière",
    "cadence haute (95-100 rpm)",
    "force-vélo (70-75 rpm)",
]

# ── Contrainte de récupération inter-hebdomadaire ────────────────────────────

def is_intensity_allowed(prev_tss: float, prev_workout_type: str, target_zone: str) -> bool:
    """
    Retourne True si une session en target_zone est autorisée après une session lourde.

    Règle : si la session précédente avait un TSS > 150 ou était un long_ride,
    la session suivante est plafonnée à Z2 (pas de Z3/Z4/Z5).
    S'applique en particulier entre le dernier jour de la semaine N (D6)
    et le premier jour de la semaine N+1 (D0).
    """
    heavy_prev = prev_tss > 150 or prev_workout_type == "long_ride"
    if heavy_prev and target_zone not in ("Z1", "Z2"):
        return False
    return True


# ── Structures d'intervalles : sourcées de sessions/*.yaml (spec 004 T034) ────
# SWEET_SPOT_STRUCTURES/THRESHOLD_STRUCTURES/VO2_STRUCTURES supprimées — leur
# contenu vit maintenant dans sessions/sweet-spot.yaml, sessions/threshold.yaml,
# sessions/vo2.yaml, éditables sans toucher ce fichier (FR-016, FR-018).

ZONE_NAMES = {
    "Z1": "Récupération active",
    "Z2": "Endurance",
    "Z3": "Tempo",
    "Z4": "Seuil lactique",
    "Z5": "VO2 Max",
    "Z6": "Anaérobie",
}


WARMUP_MIN = 15       # échauffement Z1 — sweet spot / threshold
WARMUP_VO2_MIN = 20   # échauffement étendu VO2max (inclut accélérations progressives pré-effort)
COOLDOWN_MIN = 15     # retour au calme Z1 systématique


def _week_monday(day: date) -> date:
    """Retourne le lundi de la semaine contenant `day`."""
    return day - timedelta(days=day.weekday())


def _build_interval_steps(
    sets: int, work_min: int, rest_min: int, zone: str,
    warmup_min: int = WARMUP_MIN, cooldown_min: int = COOLDOWN_MIN,
) -> list[Step | RepeatGroup]:
    """[warmup, RepeatGroup(sets × [work, recovery]), cooldown] — spec 004 T012.

    Includes a recovery after the final repetition: FR-003 asks for one repeated unit
    with a repetition count, not a special-cased last repetition, and
    contracts/session-library.md's canonical example already commits to this shape for
    contributor-authored templates. Total duration/TSS therefore runs slightly higher
    than the old `warmup + sets*work + (sets-1)*rest + cooldown` clamp did — validated
    via the eval harness (Phase 4/7), not assumed harmless (research R2).

    `cooldown_min` defaults to the standard `COOLDOWN_MIN` but is overridable —
    found live while wiring the taper "activation" template (T034): its library
    entry declares a 10-minute cooldown, shorter than the standard 15, and a
    hardcoded cooldown here would have silently disagreed with the template it
    claims to materialize."""
    return [
        Step(kind="warmup", duration_minutes=warmup_min, zone_code="Z1"),
        RepeatGroup(repeat=sets, steps=[
            Step(kind="work", duration_minutes=work_min, zone_code=zone),
            Step(kind="recovery", duration_minutes=rest_min, zone_code="Z1"),
        ]),
        Step(kind="cooldown", duration_minutes=cooldown_min, zone_code="Z1"),
    ]


def _build_steady_steps(zone: str, duration_minutes: int) -> list[Step]:
    """A session with no internal structure is still expressed as steps — one
    `steady` step — rather than a special case (FR-004)."""
    return [Step(kind="steady", duration_minutes=duration_minutes, zone_code=zone)]


def _build_activation_steps(zone: str) -> list[Step | RepeatGroup]:
    """The taper phase's short "3×8min activation" session — shorter warmup/cooldown
    than a standard interval structure since it exists to open the legs before an
    event, not accumulate training stress. Sourced from sessions/threshold.yaml's
    `taper-activation` template (spec 004 T034); the shape (sets/work/rest) comes
    from the library, materialized here so `zone` can still be overridden the same
    way threshold's Z3/Z4 gate needs."""
    template = select_template("taper", "intervals", 0, family="activation")
    sets, work, rest = _interval_shape_from_template(template)
    return _build_interval_steps(
        sets, work, rest, zone,
        warmup_min=_warmup_minutes_from_template(template),
        cooldown_min=_cooldown_minutes_from_template(template),
    )


def _structure_duration(sets: int, work: int, rest: int, warmup_min: int = WARMUP_MIN) -> int:
    """Durée totale dérivée de la structure — remplace l'ancien clamp silencieux
    `max(40, min(120, ...))` (spec 004 FR-005, T015) : la durée ne peut plus être en
    désaccord avec ses propres composants. Aucune structure actuelle (voir
    research.md R2) ne dépasse 120min même avec le rest final désormais inclus, donc
    retirer le clamp ne change aucun comportement observable au-delà du total
    lui-même."""
    return warmup_min + sets * (work + rest) + COOLDOWN_MIN


def _interval_shape_from_template(template) -> tuple[int, int, int]:
    """Extracts (sets, work_minutes, rest_minutes) from a library template's one
    RepeatGroup. plan_builder still materializes the actual steps for a session
    via `_build_interval_steps()`, since the zone can vary contextually in a way a
    static template cannot (see threshold's Z3/Z4 gate in `_build_week_template`) —
    the template supplies the *shape*, not the finished session."""
    group = next(item for item in template.structure if isinstance(item, RepeatGroup))
    work_step = next(s for s in group.steps if s.kind == "work")
    rest_step = next((s for s in group.steps if s.kind == "recovery"), None)
    rest_minutes = rest_step.duration_minutes if rest_step else 0
    return group.repeat, work_step.duration_minutes, rest_minutes


def _warmup_minutes_from_template(template) -> int:
    warmup = next(
        (item for item in template.structure if isinstance(item, Step) and item.kind == "warmup"),
        None,
    )
    return warmup.duration_minutes if warmup else WARMUP_MIN


def _cooldown_minutes_from_template(template) -> int:
    cooldown = next(
        (item for item in template.structure if isinstance(item, Step) and item.kind == "cooldown"),
        None,
    )
    return cooldown.duration_minutes if cooldown else COOLDOWN_MIN


def _get_sweet_spot(phase: str, week_in_block: int) -> tuple[int, str, int, int, int]:
    template = select_template(phase, "intervals", week_in_block, family="sweet_spot")
    sets, work, rest = _interval_shape_from_template(template)
    duration = _structure_duration(sets, work, rest, _warmup_minutes_from_template(template))
    return duration, f"{sets}×{work}min Sweet Spot", sets, work, rest


def _get_threshold(phase: str, week_in_block: int) -> tuple[int, str, int, int, int]:
    template = select_template(phase, "intervals", week_in_block, family="threshold")
    sets, work, rest = _interval_shape_from_template(template)
    duration = _structure_duration(sets, work, rest, _warmup_minutes_from_template(template))
    return duration, f"{sets}×{work}min", sets, work, rest


def _get_vo2(phase: str, week_in_block: int) -> tuple[int, str, int, int, int]:
    template = select_template(phase, "intervals", week_in_block, family="vo2")
    sets, work, rest = _interval_shape_from_template(template)
    duration = _structure_duration(sets, work, rest, _warmup_minutes_from_template(template))
    return duration, f"{sets}×{work}min VO2", sets, work, rest


def _interval_tss(
    sets: int, work_min: int, rest_min: int,
    zone: str, coaching_mode: str, ftp,
    warmup_min: int = WARMUP_MIN,
) -> float:
    """TSS total d'une séance d'intervalles structurée — calculé depuis les steps
    réels (spec 004 T012, `estimate_structured_session_tss()`) plutôt que par une
    somme ad hoc, pour garantir l'accord avec `_build_interval_steps()` (FR-006).

    warmup_min : 15 min par défaut (SS/threshold), 20 min pour VO2max.
    """
    steps = _build_interval_steps(sets, work_min, rest_min, zone, warmup_min)
    return estimate_structured_session_tss(steps, coaching_mode, ftp)


# ── Race Week ────────────────────────────────────────────────────────────────

def _build_race_week(
    race_date: date,
    available_days: list[int],
    coaching_mode: str,
    ftp: int | None,
) -> list[SessionSpec]:
    """
    Génère les séances de la semaine de course.

    Principe : atteindre TSB +10/+15 le jour de la course.
    - Séance 1 (début de semaine) : Z2 endurance 120min (~90 TSS) — maintien
      tension musculaire et sensation de jambes.
    - Séance 2 (milieu de semaine) : activation Z5 5×5min (~64 TSS) — rappels
      neuromusculaires pour éviter les "jambes de bois" sans accumuler de
      fatigue significative.
    - Veille course : repos complet.
    - Marqueur course : 20min Z2 échauffement symbolique (15 TSS) — la sortie
      réelle sera importée depuis la source de données.

    Math ATL (τ=7j) : avec ATL≈63 en fin de S10, la structure ci-dessus
    livre ATL≈41 le samedi matin → TSB ≈ +13/+14 ✓
    """
    race_dow = race_date.weekday()  # 0=Lundi … 6=Dimanche

    sessions: list[SessionSpec] = []

    # Jours disponibles avant J-2 (repos complet veille de course)
    cutoff = race_dow - 2
    pre_race_days = sorted([d for d in available_days if d <= cutoff])

    # ── Séance 1 : Z2 endurance 120min ────────────────────────────────────────
    # Zone from sessions/race-week.yaml's race-endurance template; duration is
    # this function's own concern (event-specific, not library content) — same
    # "template supplies zone, caller supplies size" split as long_ride/endurance.
    endurance_zone = select_template("taper", "endurance", 0, family="race").structure[0].zone_code
    if len(pre_race_days) >= 1:
        d0 = pre_race_days[0]
        dur0 = 120
        steps0 = _build_steady_steps(endurance_zone, dur0)
        tss0 = estimate_structured_session_tss(steps0, coaching_mode, ftp)
        sessions.append(SessionSpec(
            day_of_week=d0,
            workout_type="endurance",
            zone_code=endurance_zone,
            duration_minutes=dur0,
            target_time_in_zone_minutes=0,
            tss_target=tss0,
            description_fr=(
                "Endurance Z2 — 120min en zone 2, allure confortable. "
                "Objectif : maintenir la sensation de jambes et le tonus musculaire "
                "sans accumuler de fatigue avant la course."
            ),
            steps=steps0,
        ))

    # ── Séance 2 : activation Z5 5×5min ──────────────────────────────────────
    if len(pre_race_days) >= 2:
        d1 = pre_race_days[-1]
        # Sourced from sessions/race-week.yaml's race-activation template
        # (spec 004 T034) — fixed shape, no zone override needed (unlike threshold).
        activation_template = select_template("taper", "intervals", 0, family="race")
        sets, work_min, rest_min = _interval_shape_from_template(activation_template)
        steps1: list[Step | RepeatGroup] = list(activation_template.structure)
        total_min = derive_duration_minutes(steps1)
        tss1 = estimate_structured_session_tss(steps1, coaching_mode, ftp)
        sessions.append(SessionSpec(
            day_of_week=d1,
            workout_type="intervals",
            zone_code=derive_zone_code(steps1),
            duration_minutes=total_min,
            target_time_in_zone_minutes=derive_target_time_in_zone_minutes(steps1),
            tss_target=tss1,
            description_fr=(
                f"Activation pre-course — 20min Z2 échauffement, "
                f"puis {sets}×{work_min}min Z5 / {rest_min}min Z1 récup, "
                f"10min Z1 retour au calme. "
                "Rappels neuromusculaires pour ouvrir les jambes sans fatigue."
            ),
            steps=steps1,
        ))

    # ── Marqueur course ────────────────────────────────────────────────────────
    # tss_target ≥ 1.0 et duration_minutes ≥ 20 (contraintes tests). Zone from
    # sessions/race-week.yaml's race-day-marker template (spec 004 T034).
    marker_zone = select_template("taper", "long_ride", 0, family="race").structure[0].zone_code
    marker_steps = _build_steady_steps(marker_zone, 20)
    tss_marker = estimate_structured_session_tss(marker_steps, coaching_mode, ftp)
    sessions.append(SessionSpec(
        day_of_week=race_dow,
        workout_type="long_ride",
        zone_code=marker_zone,
        duration_minutes=20,
        target_time_in_zone_minutes=0,
        tss_target=max(tss_marker, 1.0),
        description_fr=(
            "JOUR DE COURSE — Bonne chance ! "
            "Échauffement 20min Z1-Z2 avant le départ. "
            "Ta sortie sera automatiquement importée depuis intervals.icu."
        ),
        steps=marker_steps,
    ))

    return sessions


# ── Point d'entrée ────────────────────────────────────────────────────────────

def generate_plan(profile: AthleteProfileSchema, start_date: date | None = None) -> TrainingPlanSchema:
    """Point d'entrée principal du moteur déterministe."""
    coaching_mode = profile.coaching_mode
    ftp = profile.equipment.ftp if coaching_mode == "power" else None

    if coaching_mode == "power" and ftp:
        zones = compute_power_zones(ftp)
    else:
        zones = compute_hr_zones(profile.physio.hr_max, profile.physio.hr_rest)

    current_ctl = profile.current_ctl
    current_tsb = profile.current_tsb

    # Baseline TSS depuis le CTL de la source (steady-state réel) si disponible,
    # sinon estimation depuis les heures déclarées (hypothèse Z2 dominant).
    if current_ctl is not None:
        initial_tss = round(current_ctl * 7, 1)
    else:
        initial_tss = tss_from_weekly_hours(profile.availability.hours_per_week)
    peak_tss = compute_peak_tss(initial_tss, profile.level, profile.structured_plan_history)

    today = start_date or date.today()
    week_start = _week_monday(today)

    # Démarrage au lendemain de la fin de l'onboarding.
    # Si aujourd'hui est dimanche, on passe directement au lundi suivant (semaine complète).
    tomorrow_dow = today.weekday() + 1
    if tomorrow_dow >= 7:
        week_start += timedelta(weeks=1)
        min_day_week1 = 0
    else:
        min_day_week1 = tomorrow_dow

    target_date = profile.objective.target_date
    if target_date and target_date > week_start:
        weeks_total = max(4, (target_date - week_start).days // 7)
    else:
        weeks_total = 12
    weeks_total = min(weeks_total, 52)

    phases = compute_phase_sequence(weeks_total, current_ctl=current_ctl)
    weekly_targets = compute_weekly_tss_targets(
        initial_tss, peak_tss, phases,
        initial_ctl=current_ctl,
        level=profile.level,
    )
    week_annotations = _annotate_weeks(weekly_targets)

    preferred = profile.availability.preferred_days
    available_days = sorted(
        [DAY_NAMES.index(d) for d in preferred if d in DAY_NAMES]
    )
    if not available_days:
        available_days = [1, 3, 5, 6]

    hours_per_week = profile.availability.hours_per_week
    week_plans: list[WeekPlan] = []
    current_week_start = week_start

    # Tracking inter-hebdomadaire : dernière session de la semaine précédente
    prev_last_tss: float = 0.0
    prev_last_type: str = ""

    for week_num, ((phase, tss_target, is_recovery), (phase_progress, week_in_block)) in enumerate(
        zip(weekly_targets, week_annotations), start=1
    ):
        # La semaine précédente s'est-elle terminée par une session lourde ?
        cross_week_heavy = not is_intensity_allowed(prev_last_tss, prev_last_type, "Z3")

        # Semaine 1 : ne garder que les jours >= lendemain de l'onboarding.
        # Si aucun jour dispo dans le reste de la semaine, sauter au lundi suivant
        # (la semaine est trop entamée — équivalent Option A pour ce cas précis).
        if week_num == 1:
            effective_days = [d for d in available_days if d >= min_day_week1]
            if not effective_days:
                current_week_start += timedelta(weeks=1)
                effective_days = available_days
        else:
            effective_days = available_days

        sessions = _build_sessions(
            phase=phase,
            tss_target=tss_target,
            is_recovery=is_recovery,
            available_days=effective_days,
            coaching_mode=coaching_mode,
            ftp=ftp,
            hours_per_week=hours_per_week,
            phase_progress=phase_progress,
            week_in_block=week_in_block,
            cross_week_heavy=cross_week_heavy,
            current_ctl=current_ctl,
            current_tsb=current_tsb,
            structured_plan_history=profile.structured_plan_history,
            level=profile.level,
        )

        # Mémoriser la dernière session (jour le plus élevé = fin de semaine)
        if sessions:
            last = max(sessions, key=lambda s: s.day_of_week)
            prev_last_tss = last.tss_target
            prev_last_type = last.workout_type
        week_plans.append(WeekPlan(
            week_number=week_num,
            phase=phase,
            is_recovery_week=is_recovery,
            total_tss_target=tss_target,
            sessions=sessions,
            start_date=current_week_start,
        ))
        current_week_start += timedelta(weeks=1)

    # ── Race Week : semaine de course explicite ────────────────────────────────
    # Si la date cible tombe dans les 7 jours suivant la fin du plan (plan construit
    # avec floor), ou si elle tombe dans la dernière semaine (plan avec ceil), on
    # s'assure qu'une Race Week légère figure dans le plan avec la course le bon jour.
    # La Race Week contient 2-3 séances très légères (Z1/Z2) en début de semaine,
    # puis silence avant la course — l'athlète doit voir ce qu'il fait le jour J.
    plan_last_day = current_week_start - timedelta(days=1)
    # Race Week uniquement si la course est au moins 3 jours après la fin du plan
    # (gap < 3j = la course est "le lendemain du taper" → pas de semaine supplémentaire utile)
    if target_date and (target_date - plan_last_day).days >= 3:
        # La course déborde : ajouter une Race Week explicite
        race_week_sessions = _build_race_week(
            target_date, available_days, coaching_mode, ftp
        )
        race_tss = sum(s.tss_target for s in race_week_sessions)
        week_plans.append(WeekPlan(
            week_number=weeks_total + 1,
            phase="taper",
            is_recovery_week=False,
            total_tss_target=race_tss,
            sessions=race_week_sessions,
            start_date=current_week_start,
        ))
        weeks_total += 1
        current_week_start += timedelta(weeks=1)

    return TrainingPlanSchema(
        weeks=week_plans,
        zones=zones,
        initial_weekly_tss=initial_tss,
        peak_weekly_tss=peak_tss,
        weeks_count=weeks_total,
        coaching_mode=coaching_mode,
        start_date=week_start,
        end_date=current_week_start - timedelta(days=1),
    )


# ── Annotation des semaines ───────────────────────────────────────────────────

def _annotate_weeks(
    weekly_targets: list[tuple[str, float, bool]],
) -> list[tuple[float, int]]:
    """
    Pour chaque semaine : (phase_progress ∈ [0,1], week_in_block ∈ {0,1,2,3}).

    week_in_block est maintenant synchronisé avec charge_week dans periodization.py :
      0 = semaine nominale   (×1.00) → sortie longue facteur 0.90
      1 = semaine charge     (×1.10) → sortie longue facteur 1.00
      2 = semaine surcharge  (×1.20) → sortie longue facteur 1.10 (la plus longue)
      3 = semaine récupération → sortie longue facteur 0.65 (mais jamais atteint :
          les semaines récup passent par le branch is_recovery et n'appellent pas _long_ride_duration)

    L'ancienne implémentation utilisait (i + k) % 4 — un décalage arithmétique sans rapport
    avec le pattern de récupération réel. Résultat : en BUILD S5 (la semaine la plus dure,
    charge_week=1 → ×1.10), week_in_block était 3 → sortie longue à 156 min au lieu de 240 min.
    Ce desynchronisme réduisait le TSS livré et empêchait d'atteindre TSB < -20.
    """
    result: list[tuple[float, int]] = []
    wib = 0  # week_in_block, réinitialisé après chaque semaine de récup (= charge_week)

    i = 0
    while i < len(weekly_targets):
        phase = weekly_targets[i][0]
        j = i
        while j < len(weekly_targets) and weekly_targets[j][0] == phase:
            j += 1
        phase_len = j - i
        for k in range(phase_len):
            progress = k / max(phase_len - 1, 1)
            _, _, is_recovery = weekly_targets[i + k]
            if is_recovery:
                result.append((progress, 3))
                wib = 0  # reset comme charge_week après récup
            else:
                result.append((progress, wib % 3))
                wib += 1
        i = j
    return result


# ── Volume → nombre de séances ───────────────────────────────────────────────

def _sessions_from_hours(hours: float) -> int:
    if hours <= 3:  return 2
    if hours <= 5:  return 3
    if hours <= 8:  return 4
    if hours <= 11: return 5
    return 6


# ── Sélection des jours d'entraînement ───────────────────────────────────────

def _select_training_days(available_days: list[int], n_sessions: int) -> list[int]:
    """
    Sélectionne n_sessions jours parmi les disponibles.
    Priorité au week-end (pour la sortie longue), puis espacement maximum.
    """
    if n_sessions >= len(available_days):
        return list(available_days)

    selected: list[int] = []
    weekend = [d for d in available_days if d >= 5]
    if weekend:
        selected.append(max(weekend))

    pool = [d for d in available_days if d not in selected]
    while len(selected) < n_sessions and pool:
        best = max(pool, key=lambda d: min(abs(d - s) for s in selected) if selected else 0)
        selected.append(best)
        pool.remove(best)

    return sorted(selected)


# ── Durée de la sortie longue ─────────────────────────────────────────────────

def _long_ride_duration(hours_per_week: float, phase: str, week_in_block: int) -> int:
    """
    Durée de la sortie longue.
    - Taper : 25 % du volume (volume réduit)
    - Autres : 40 % avec variation 90/100/110/65 % selon week_in_block
    """
    if phase == "taper":
        minutes = int(hours_per_week * 0.25 * 60)
        return max(45, min(150, minutes))
    base = int(hours_per_week * 0.40 * 60)
    factors = {0: 0.90, 1: 1.00, 2: 1.10, 3: 0.65}
    return max(60, min(300, int(base * factors.get(week_in_block % 4, 1.00))))


# ── Template de semaine (composition priorisée) ───────────────────────────────

def _build_week_template(
    phase: str,
    n_sessions: int,
    phase_progress: float,
    week_in_block: int,
    current_ctl: float | None = None,
    current_tsb: float | None = None,
    structured_plan_history: bool = False,
) -> list[tuple[str, str, str]]:
    """
    Retourne les n_sessions sessions de la semaine, par ordre de priorité décroissante.
    Format : [(workout_type, zone, detail), ...]

    Priorité par phase :
    - Base  : long_ride ► endurance ► sweet_spot (si phase tardive) ► recovery
    - Build : long_ride ► threshold ► endurance ► VO2 ► recovery ► endurance
    - Peak  : long_ride ► VO2 ► threshold ► endurance ► recovery ► endurance
    - Taper : long_ride ► intervals_activation ► recovery ► endurance

    La VO2 n'apparaît en build qu'à partir de n≥4 sessions (une endurance passe avant).
    """
    # (priority_desc, workout_type, zone, detail)
    cands: list[tuple[float, str, str, str]] = []

    # Variantes d'endurance : rotation pour éviter des séances identiques
    ev0 = ENDURANCE_VARIANTS[week_in_block % 3]
    ev1 = ENDURANCE_VARIANTS[(week_in_block + 1) % 3]
    ev2 = ENDURANCE_VARIANTS[(week_in_block + 2) % 3]

    if phase == "base":
        # Seuils adaptés au CTL actuel ET à l'historique structuré :
        # - CTL élevé (≥50) + historique structuré → sweet spot dès sem. 2, portions Z3 tôt
        # - CTL élevé (≥50) + PAS d'historique structuré → délai plus long (l'athlète a du volume
        #   mais sans travail structuré, le système neuro-musculaire n'est pas habitué aux intervalles)
        # - CTL moyen (35-50) → sweet spot au quart de la base
        # - CTL faible ou inconnu → comportement original
        if current_ctl and current_ctl >= 50 and structured_plan_history:
            ss_threshold = 0.10   # sweet spot dès la 2e semaine de base
            lr_z3_threshold = 0.25
        elif current_ctl and current_ctl >= 50:
            ss_threshold = 0.35   # pas d'historique structuré → sweet spot seulement en seconde moitié
            lr_z3_threshold = 0.50
        elif current_ctl and current_ctl >= 35:
            ss_threshold = 0.25
            lr_z3_threshold = 0.45
        else:
            ss_threshold = 0.45   # comportement original
            lr_z3_threshold = 0.65

        lr_detail = "avec portions Z3 optionnelles" if phase_progress >= lr_z3_threshold else ""
        cands.append((10.0, "long_ride", "Z2", lr_detail))

        if phase_progress >= ss_threshold:
            _, label, *_ = _get_sweet_spot(phase, week_in_block)
            cands.append((7.0, "intervals", "Z3", label))

        cands += [
            (9.0, "endurance", "Z2", ev0),
            (6.0, "endurance", "Z2", ev1),
            (5.0, "endurance", "Z2", ev2),
            (2.0, "recovery",  "Z1", ""),
        ]

    elif phase == "build":
        # Long ride Z2 — base aérobie avec blocs au seuil intégrés en fin de sortie.
        # Spécificité cyclosportive : simuler les cols en intégrant 2-3 blocs Z4
        # de 10-15min (ex: 10min de montée au seuil, récup active en descente).
        cands.append((10.0, "long_ride", "Z2", "avec 2-3 blocs au seuil Z4 (10-15min) simulant une montée de col"))

        # Z4 (seuil lactique) introduit dès la 2e semaine de build pour les profils
        # cyclosportive. L'ancien seuil 0.35 repoussait Z4 aux 2 dernières semaines
        # de build, laissant trop de Tempo/Sweet Spot (Z3) sans travail spécifique col.
        th_zone = "Z4" if phase_progress >= 0.20 else "Z3"
        _, th_label, *_ = _get_threshold(phase, week_in_block)
        cands.append((9.0, "intervals", th_zone, th_label))

        # Une endurance avant VO2 : pour n=3 → threshold + endurance (pas VO2)
        cands.append((8.0, "endurance", "Z2", ev0))

        _, vo2_label, *_ = _get_vo2(phase, week_in_block)
        cands.append((7.0, "intervals", "Z5", vo2_label))

        cands += [
            (6.0, "recovery",  "Z1", ""),
            (5.0, "endurance", "Z2", ev1),
        ]

    elif phase == "peak":
        # Long ride Z2 — en peak, le volume est réduit mais l'intensité ciblée.
        # Cyclosportive : simuler les conditions de course avec 2-3 cols au seuil Z4.
        cands.append((10.0, "long_ride", "Z2", "avec blocs specifiques au seuil Z4 (simuler les cols de la course)"))

        _, vo2_label, *_ = _get_vo2(phase, week_in_block)
        cands.append((9.0, "intervals", "Z5", vo2_label))

        _, th_label, *_ = _get_threshold(phase, week_in_block)
        cands.append((8.0, "intervals", "Z4", th_label))

        cands += [
            (7.0, "endurance", "Z2", ev0),
            (6.0, "recovery",  "Z1", ""),
            (5.0, "endurance", "Z2", ev1),
        ]

    else:  # taper
        cands.append((10.0, "long_ride", "Z2", ""))
        cands.append((9.0,  "intervals", "Z4", "3×8min activation"))
        cands += [
            (7.0, "recovery",  "Z1", ""),
            (6.0, "endurance", "Z2", ev0),
            (5.0, "endurance", "Z2", ev1),
        ]

    cands.sort(key=lambda x: -x[0])
    return [(t, z, d) for _, t, z, d in cands[:n_sessions]]


# ── Assignation aux jours ─────────────────────────────────────────────────────

def _assign_sessions_to_days(
    training_days: list[int],
    templates: list[tuple[str, str, str]],
    cross_week_heavy: bool = False,
) -> list[tuple[int, str, str, str]]:
    """
    Assigne les sessions aux jours d'entraînement.
    Règles physiologiques :
    - long_ride → dernier jour
    - Z4/Z5 (intervalles haute intensité) → gap ≥ 2 jours entre eux ET avec long_ride
    - Slot avant long_ride → session facile (recovery ou endurance)
    - recovery → prioritaire dans le remplissage
    - cross_week_heavy=True → premier slot de la semaine plafonné à Z2
      (la semaine précédente s'est terminée par un long_ride ou TSS > 150)
    """
    n = len(training_days)
    result: list[tuple[int, str, str, str] | None] = [None] * n

    lr = [(t, z, d) for t, z, d in templates if t == "long_ride"]
    intervals = [(t, z, d) for t, z, d in templates if t == "intervals"]
    others = [(t, z, d) for t, z, d in templates if t not in ("long_ride", "intervals")]
    others.sort(key=lambda x: 0 if x[0] == "recovery" else 1)

    # 1. long_ride → dernier slot
    lr_day = training_days[-1] if lr else -99
    intense_days: list[int] = []
    if lr:
        result[-1] = (training_days[-1], *lr[0])
        intense_days.append(lr_day)
    free = [i for i in range(n) if result[i] is None]

    # 2. Intervalles haute intensité → gap ≥ 2j avec séances intenses et long_ride
    for t, z, d in intervals:
        is_hi = z in HIGH_INTENSITY_ZONES

        # Essai 1 : slot respectant toutes les contraintes physiologiques
        best_slot: int | None = None
        best_dist = -1
        for slot in free:
            day = training_days[slot]
            dist = min((abs(day - pid) for pid in intense_days), default=99)
            # Pour Z4/Z5 : éviter le slot immédiatement avant le long_ride
            if is_hi and abs(day - lr_day) <= 1:
                continue
            if is_hi and dist < 2:
                continue
            # Contrainte inter-hebdomadaire : premier slot de la semaine plafonné à Z2
            # si la semaine précédente s'est terminée par une session lourde
            if cross_week_heavy and slot == 0 and not is_intensity_allowed(999, "long_ride", z):
                continue
            if dist > best_dist:
                best_dist = dist
                best_slot = slot

        # Essai 2 (fallback) : meilleur slot disponible sans contrainte 48h stricte.
        # Si même le meilleur slot crée une paire consécutive (<2j), on dégrade la session
        # en endurance plutôt que de générer une violation physiologique.
        if best_slot is None:
            fallback_best: int | None = None
            fallback_dist = -1
            for slot in free:
                day = training_days[slot]
                dist = min((abs(day - pid) for pid in intense_days), default=99)
                # Respecter quand même la contrainte cross_week sur slot 0
                if is_hi and cross_week_heavy and slot == 0 and not is_intensity_allowed(999, "long_ride", z):
                    continue
                if dist > fallback_dist:
                    fallback_dist = dist
                    fallback_best = slot

            if fallback_best is not None and is_hi and fallback_dist < 2:
                # Impossible de placer sans créer une paire consécutive → dégrader en endurance
                others.append(("endurance", "Z2", ""))
                fallback_best = None

            best_slot = fallback_best

        if best_slot is not None:
            result[best_slot] = (training_days[best_slot], t, z, d)
            if is_hi:
                intense_days.append(training_days[best_slot])
            free.remove(best_slot)

    # 3. Slot juste avant long_ride → session facile en priorité
    pre_lr_slot = n - 2
    if lr and pre_lr_slot >= 0 and pre_lr_slot in free:
        rec_candidates = [i for i, (wt, _, _) in enumerate(others) if wt == "recovery"]
        end_candidates = [i for i, (wt, _, _) in enumerate(others) if wt == "endurance"]
        pick_idx = (rec_candidates + end_candidates)[0] if (rec_candidates or end_candidates) else None
        if pick_idx is not None:
            result[pre_lr_slot] = (training_days[pre_lr_slot], *others[pick_idx])
            free.remove(pre_lr_slot)
            others = [o for i, o in enumerate(others) if i != pick_idx]

    # 4. Remplir le reste
    fill_idx = 0
    for slot in free:
        if fill_idx < len(others):
            result[slot] = (training_days[slot], *others[fill_idx])
            fill_idx += 1

    return [r for r in result if r is not None]


# ── Durée → TSS et durée ─────────────────────────────────────────────────────

def _tss_to_duration(tss: float, zone: str, coaching_mode: str, ftp: int | None) -> int:
    from app.engine.tss import ZONE_IF, ZONE_TSS_PER_HOUR_HR
    if coaching_mode == "power" and ftp:
        if_f = ZONE_IF.get(zone, 0.65)
        if if_f == 0:
            return 60
        dur_sec = (tss * ftp * 3600) / (ftp * if_f * if_f * 100)
        return max(20, min(300, int(dur_sec / 60)))
    tph = ZONE_TSS_PER_HOUR_HR.get(zone, 45)
    return max(20, min(300, int((tss / tph) * 60))) if tph else 60


def _session_description(wtype: str, zone: str, detail: str = "", coaching_mode: str = "hr") -> str:
    """
    Génère la description française d'une séance.
    En mode HR + Z6, ajoute automatiquement une note RPE (inertie cardiaque trop élevée).
    """
    zone_name = ZONE_NAMES.get(zone, zone)
    if wtype == "long_ride":
        base = f"Sortie longue {zone} ({zone_name})"
        desc = f"{base} — {detail}" if detail else base
    elif wtype == "endurance":
        base = f"Endurance {zone} ({zone_name})"
        desc = f"{base} — {detail}" if detail else base
    elif wtype == "recovery":
        desc = f"Récupération active {zone} ({zone_name})"
    elif detail:
        desc = f"Intervalles {zone} ({zone_name}) — {detail}"
    else:
        desc = f"Intervalles {zone} ({zone_name})"
    if zone in ("Z5", "Z6") and coaching_mode == "hr":
        desc += " (Pilotage au RPE 9/10 — le cardio monte trop lentement sur ces intervalles courts ; suivre la sensation d'effort, pas la FC)"
    return desc


# ── Constructeur de semaine ───────────────────────────────────────────────────

def _build_sessions(
    phase: str,
    tss_target: float,
    is_recovery: bool,
    available_days: list[int],
    coaching_mode: str,
    ftp: int | None,
    hours_per_week: float,
    phase_progress: float,
    week_in_block: int,
    cross_week_heavy: bool = False,
    current_ctl: float | None = None,
    current_tsb: float | None = None,
    structured_plan_history: bool = False,
    level: str = "intermediate",
) -> list[SessionSpec]:
    """
    Construit les séances d'une semaine.

    Semaine de récupération : séances Z1/Z2 courtes uniquement.
    Semaine normale :
      1. Nombre de séances = f(volume)
      2. Composition = template priorisé par phase
      3. Assignation aux jours (anti-intensité consécutive)
      4. Durée de la sortie longue variable (bloc 3+1)
      5. Intervalles structurés avec durée fixe depuis la structure
      6. Endurance/recovery : durée depuis TSS résiduel
    """
    n_available = len(available_days)
    effective_hours = hours_per_week * 0.60 if is_recovery else hours_per_week
    n_sessions = max(2, min(_sessions_from_hours(effective_hours), n_available))
    training_days = _select_training_days(available_days, n_sessions)

    # ── Semaine de récupération ───────────────────────────────────────────────
    if is_recovery:
        sessions = []
        insert_sprint = (
            current_ctl is not None
            and current_ctl >= 50
            and len(training_days) >= 3
        )
        sprint_inserted = False

        # Répartir le TSS cible entre les séances de récup.
        # Ancienne version : durée fixe 60min Z2 → 45 TSS/séance max → ~130 TSS total,
        # soit 35% du TSS de maintien (CTL×7). Le CTL s'effondrait à chaque récup.
        # Nouvelle version : TSS dimensionné depuis tss_target (calculé en periodization.py
        # à 70-82% de la semaine précédente), capé à 150min/séance pour rester récupérateur.
        fixed_tss = 15.0 + (25.0 if insert_sprint else 0.0)  # Z1 + Z6 sprint
        n_endurance = len(training_days) - 1 - int(insert_sprint)  # sessions Z2 restantes
        remaining = max(0.0, tss_target - fixed_tss)
        tss_per_end = remaining / max(n_endurance, 1)

        for i, day in enumerate(training_days):
            if insert_sprint and not sprint_inserted and i == 1:
                # Sourced from sessions/recovery.yaml's recovery-sprint-activation
                # template (spec 004 T034). Its steps use 1-minute granularity — the
                # coarsest unit Step.duration_minutes supports — as the closest
                # structural representation of a 30-second sprint; the description
                # keeps the real prescribed duration.
                sprint_template = select_template(phase, "intervals", 0, family="sprint_activation")
                sprint_steps: list[Step | RepeatGroup] = list(sprint_template.structure)
                sessions.append(SessionSpec(
                    day_of_week=day,
                    workout_type="intervals",
                    zone_code="Z6",
                    duration_minutes=derive_duration_minutes(sprint_steps),
                    target_time_in_zone_minutes=derive_target_time_in_zone_minutes(sprint_steps),
                    tss_target=estimate_structured_session_tss(sprint_steps, coaching_mode, ftp),
                    description_fr=(
                        "Activation neuromusculaire Z6 — 15min echauffement Z2, "
                        "3x30'' sprint max relance (recuperation 3min Z1 entre chaque), "
                        "10min retour calme. "
                        "Objectif : conserver le punch neuromusculaire sans stresser l'organisme."
                    ),
                    steps=sprint_steps,
                ))
                sprint_inserted = True
            elif i == 0:
                # Premier jour : Z1 actif (jambes légères)
                z1_steps = _build_steady_steps("Z1", 45)
                sessions.append(SessionSpec(
                    day_of_week=day,
                    workout_type="recovery",
                    zone_code="Z1",
                    duration_minutes=45,
                    target_time_in_zone_minutes=0,
                    tss_target=estimate_structured_session_tss(z1_steps, coaching_mode, ftp),
                    description_fr=_session_description("recovery", "Z1", coaching_mode=coaching_mode),
                    steps=z1_steps,
                ))
            else:
                # Sessions Z2 endurance : durée dynamique depuis TSS résiduel, cap 150min
                dur = _tss_to_duration(tss_per_end, "Z2", coaching_mode, ftp)
                dur = min(dur, 150)
                final_dur = max(45, dur)
                end_steps = _build_steady_steps("Z2", final_dur)
                tss = estimate_structured_session_tss(end_steps, coaching_mode, ftp)
                sessions.append(SessionSpec(
                    day_of_week=day,
                    workout_type="endurance",
                    zone_code="Z2",
                    duration_minutes=final_dur,
                    target_time_in_zone_minutes=0,
                    tss_target=max(20.0, tss),
                    description_fr=_session_description("endurance", "Z2", "récupération active", coaching_mode=coaching_mode),
                    steps=end_steps,
                ))
        return sessions

    # ── Semaine normale ───────────────────────────────────────────────────────

    # 1. Template de session priorisé par phase
    templates = _build_week_template(phase, n_sessions, phase_progress, week_in_block, current_ctl, current_tsb, structured_plan_history)

    # 2. Assignation aux jours
    assigned = _assign_sessions_to_days(training_days, templates, cross_week_heavy)

    # 3. Calculer le TSS de la sortie longue et des intervalles (durée fixe)
    #    pour en déduire le TSS résiduel pour endurance/recovery
    lr_duration = _long_ride_duration(hours_per_week, phase, week_in_block)

    fixed_tss = 0.0  # TSS des séances à durée fixe (long_ride + intervals)
    fixed_count = 0

    for _, wtype, zone, _ in assigned:
        if wtype == "long_ride":
            fixed_tss += estimate_session_tss(zone, lr_duration, coaching_mode, ftp)
            fixed_count += 1
        elif wtype == "intervals":
            fixed_count += 1  # durée calculée en step 4, on comptera après

    # Pré-calculer steps + TSS des intervals — une seule fois, réutilisés tels quels
    # dans la boucle de construction ci-dessous, pour garantir que le SessionSpec final
    # et son tss_target proviennent exactement des mêmes steps (spec 004 T012, FR-006).
    interval_steps_list: list[list[Step | RepeatGroup]] = []
    interval_tss_list: list[float] = []
    for _, wtype, zone, detail in assigned:
        if wtype == "intervals":
            if "VO2" in detail:
                _, _, sets, work, rest = _get_vo2(phase, week_in_block)
                isteps = _build_interval_steps(sets, work, rest, zone, warmup_min=WARMUP_VO2_MIN)
            elif "Sweet" in detail or "Spot" in detail:
                _, _, sets, work, rest = _get_sweet_spot(phase, week_in_block)
                isteps = _build_interval_steps(sets, work, rest, zone)
            elif "activation" in detail:
                isteps = _build_activation_steps(zone)
            else:
                _, _, sets, work, rest = _get_threshold(phase, week_in_block)
                isteps = _build_interval_steps(sets, work, rest, zone)
            itss = estimate_structured_session_tss(isteps, coaching_mode, ftp)
            interval_steps_list.append(isteps)
            interval_tss_list.append(itss)
            fixed_tss += itss

    # TSS résiduel pour endurance/recovery (min 1.0 par séance)
    n_fill = sum(1 for _, wtype, _, _ in assigned if wtype in ("endurance", "recovery"))
    remaining_tss = max(n_fill * 5.0, tss_target - fixed_tss)
    # Distribuer : endurance 1.0×, recovery 0.4×
    fill_weights = [
        1.0 if wtype == "endurance" else 0.4
        for _, wtype, _, _ in assigned
        if wtype in ("endurance", "recovery")
    ]
    tw = sum(fill_weights) or 1.0
    fill_tss_list = [round(remaining_tss * w / tw, 1) for w in fill_weights]

    # Pour les athlètes advanced/expert en semaines de charge (build/peak) :
    # la plus grande séance d'endurance monte en Z3 (Tempo, 65 TSS/h vs 45 TSS/h en Z2).
    # Cela augmente le TSS livré de ~80 TSS sur 4h sans ajouter de minutes,
    # indispensable pour viser CTL 65-70 sur 10h/sem.
    # Index fill (dans fill_tss_list) de la plus grosse endurance à upgrader en Z3.
    biggest_z3_idx: int | None = None
    if level in ("advanced", "expert") and phase in ("build", "peak"):
        fill_enum = [
            (k, wtype)
            for k, (_, wtype, _, _) in enumerate(
                [t for t in assigned if t[1] in ("endurance", "recovery")]
            )
        ]
        end_fill_indices = [k for k, wtype in fill_enum if wtype == "endurance"]
        if end_fill_indices:
            biggest_z3_idx = max(end_fill_indices, key=lambda k: fill_tss_list[k])

    # 4. Construire les SessionSpec
    sessions: list[SessionSpec] = []
    int_idx = 0
    fill_idx = 0

    for day, wtype, zone, detail in assigned:
        steps: list[Step | RepeatGroup] | None = None

        if wtype == "long_ride":
            duration = lr_duration
            tss = estimate_session_tss(zone, lr_duration, coaching_mode, ftp)
            tss = min(tss, tss_target * 0.55)

        elif wtype == "intervals":
            steps = interval_steps_list[int_idx]
            duration = derive_duration_minutes(steps)
            tss = interval_tss_list[int_idx]
            int_idx += 1

        else:  # endurance / recovery
            is_biggest_end = (fill_idx == biggest_z3_idx)
            # Upgrade en Z3 pour la plus grande séance endurance des semaines dures advanced :
            # Z3 Tempo (65 TSS/h) vs Z2 (45 TSS/h) → +44% TSS à durée égale.
            eff_zone = "Z3" if (wtype == "endurance" and is_biggest_end) else zone
            tss = fill_tss_list[fill_idx] if fill_idx < len(fill_tss_list) else 5.0
            fill_idx += 1
            duration = _tss_to_duration(tss, eff_zone, coaching_mode, ftp)
            if wtype == "recovery":
                duration = min(duration, 60)
                tss = estimate_session_tss(eff_zone, duration, coaching_mode, ftp)
            elif wtype == "endurance":
                duration = min(duration, 270)
                tss = estimate_session_tss(eff_zone, duration, coaching_mode, ftp)
            zone = eff_zone  # propagate to SessionSpec

        final_duration = max(20, duration)

        # long_ride and endurance/recovery have no internal structure — one `steady`
        # step, built here from the final (post-clamp) duration so it can never
        # disagree with duration_minutes (FR-004, FR-005). Interval sessions already
        # have their steps from the precompute pass above.
        if steps is None:
            steps = _build_steady_steps(zone, final_duration)

        sessions.append(SessionSpec(
            day_of_week=day,
            workout_type=wtype,
            zone_code=zone,
            duration_minutes=final_duration,
            target_time_in_zone_minutes=derive_target_time_in_zone_minutes(steps),
            tss_target=max(1.0, tss),
            description_fr=_session_description(wtype, zone, detail, coaching_mode=coaching_mode),
            steps=steps,
        ))

    # ── Garde-fou volume : total minutes ≤ hours_per_week × budget_factor ─────
    # Advanced/expert : 1.10 (11% de dépassement toléré — semaines dures légitimement
    # plus longues que la moyenne déclarée). Autres : 1.05.
    budget_factor = 1.10 if level in ("advanced", "expert") else 1.05
    budget_min = int(hours_per_week * 60 * budget_factor)
    total_min = sum(s.duration_minutes for s in sessions)
    if total_min > budget_min:
        flex = [(i, s) for i, s in enumerate(sessions) if s.workout_type in ("endurance", "recovery")]
        flex_total = sum(s.duration_minutes for _, s in flex)
        overshoot = total_min - budget_min
        if flex_total > overshoot:
            scale = (flex_total - overshoot) / flex_total
            new_sessions = list(sessions)
            for idx, s in flex:
                new_dur = max(20, int(s.duration_minutes * scale))
                new_tss = estimate_session_tss(s.zone_code, new_dur, coaching_mode, ftp)
                # endurance/recovery are always steady (single-step) sessions — rebuild
                # that step at the rescaled duration so it cannot disagree (FR-005).
                new_steps = _build_steady_steps(s.zone_code, new_dur)
                new_sessions[idx] = SessionSpec(
                    day_of_week=s.day_of_week,
                    workout_type=s.workout_type,
                    zone_code=s.zone_code,
                    duration_minutes=new_dur,
                    target_time_in_zone_minutes=derive_target_time_in_zone_minutes(new_steps),
                    tss_target=max(1.0, new_tss),
                    description_fr=s.description_fr,
                    steps=new_steps,
                )
            sessions = new_sessions

    return sessions
