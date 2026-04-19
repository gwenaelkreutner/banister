#!/usr/bin/env python3
"""
Boucle d'évaluation autonome du plan Banister.

Génère le plan depuis un profil JSON, l'évalue contre des critères qualitatifs
cyclisme, et affiche un rapport détaillé avec les problèmes et leur sévérité.

Usage :
    python scripts/eval_plan.py --profile scripts/me.json --start-date 2026-03-09
    python scripts/eval_plan.py --profile scripts/me.json --start-date 2026-03-09 --verbose
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.atl_ctl import FitnessProjectionPoint, project_fitness_from_plan
from app.engine.plan_builder import generate_plan
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema


# ─── Résultat d'un critère ─────────────────────────────────────────────────────

@dataclass
class EvalResult:
    passed: bool
    score: float        # 0.0 → 1.0
    name: str           # nom court du critère
    message: str        # résumé
    issues: list[str]   # détail des problèmes (vide si passed)
    fixes: list[str]    # suggestions de correction (vide si passed)


# ─── Critère 1 : CTL — stabilité et progression ───────────────────────────────

def check_ctl_stability(
    plan: TrainingPlanSchema,
    projection: list[FitnessProjectionPoint] | None,
    profile: AthleteProfileSchema,
) -> EvalResult:
    """
    Progression CTL et forme de course.

    Note : la CTL finale est INFÉRIEURE à la CTL initiale par design (taper).
    C'est voulu : on accepte de perdre quelques pts de CTL pour arriver frais.
    On évalue donc :
    1. Pic CTL ≥ CTL initiale × 1.01 — le plan a réellement construit de la forme
    2. TSB en fin de plan ∈ [+3, +20] — zone de fraîcheur pour une course
    3. Phase PEAK jamais marquée recovery — le point culminant doit rester intense
    4. Aucune semaine normale (hors récup/taper) ne fait chuter CTL > 4 pts
    """
    name = "CTL : progression et forme de course"

    if not projection:
        return EvalResult(True, 1.0, name, "Pas de données Strava — skip", [], [])

    initial_ctl = profile.current_ctl
    proj_by_week = {p.week_number: p for p in projection}
    issues: list[str] = []
    fixes: list[str] = []

    final_ctl = projection[-1].ctl
    final_tsb = projection[-1].tsb
    peak_ctl = max(p.ctl for p in projection)
    peak_week = max(projection, key=lambda p: p.ctl).week_number

    # 1. Le plan doit construire de la forme (pic CTL > initial)
    if peak_ctl < initial_ctl * 1.01:
        issues.append(
            f"CTL pic={peak_ctl:.1f} <= initiale={initial_ctl:.1f} — "
            "le plan ne construit aucune forme nouvelle"
        )
        fixes.append(
            "Augmenter le volume des semaines de charge ou réduire le facteur de récup. "
            "Vérifier que la phase PEAK n'est pas marquée is_recovery"
        )

    # 2. TSB au jour de course dans la fenêtre de performance (taper réussi)
    # Le plan peut se terminer quelques jours AVANT la date cible. Dans ce cas,
    # on simule le déclin ATL/CTL (τ=7j/42j) sur les jours de repos restants
    # pour évaluer la fraîcheur réelle le jour J, pas à la fin du plan.
    import math
    plan_end_ctl = projection[-1].ctl
    plan_end_atl = projection[-1].atl
    plan_end_date = projection[-1].date

    # Jours restants entre fin de plan et date cible (si définie)
    target_date = profile.objective.target_date
    rest_days = (target_date - plan_end_date).days if target_date else 0
    rest_days = max(0, rest_days)

    if rest_days > 0:
        # Simulation déclin ATL/CTL sur jours de repos (TSS=0)
        race_ctl = plan_end_ctl * math.exp(-rest_days / 42)
        race_atl = plan_end_atl * math.exp(-rest_days / 7)
        race_tsb = race_ctl - race_atl
        tsb_label = f"TSB plan={final_tsb:+.1f} → TSB J-course (+{rest_days}j repos)={race_tsb:+.1f}"
    else:
        race_tsb = final_tsb
        tsb_label = f"TSB final={final_tsb:+.1f}"

    if race_tsb < 3:
        issues.append(
            f"{tsb_label} < +3 — taper insuffisant, "
            "l'athlète n'est pas assez frais pour la course"
        )
        fixes.append(
            "Allonger le taper ou réduire davantage le volume de la dernière semaine"
        )
    elif race_tsb > 25:
        issues.append(
            f"{tsb_label} > +25 — taper trop long, "
            "risque de désentraînement avant la course"
        )
        fixes.append("Raccourcir le taper ou maintenir plus de volume sur la dernière semaine")

    # 3. Phase PEAK jamais en recovery
    for week in plan.weeks:
        if week.phase == "peak" and week.is_recovery_week:
            issues.append(
                f"S{week.week_number} PEAK est marquée RECOVERY — "
                "la semaine de pic est transformée en décharge (bug critique)"
            )
            fixes.append(
                "Dans periodization.py, exclure 'peak' de la condition is_recovery : "
                "phase.phase not in ('taper', 'peak')"
            )

    # 4. Chute CTL anormale sur une semaine normale
    prev_ctl = initial_ctl
    for week in plan.weeks:
        wn = week.week_number
        if wn not in proj_by_week:
            continue
        p = proj_by_week[wn]
        drop = prev_ctl - p.ctl
        if not week.is_recovery_week and week.phase not in ("taper",) and drop > 4.0:
            issues.append(
                f"S{wn} ({week.phase.upper()}): CTL chute de {drop:.1f} pts "
                f"({prev_ctl:.1f} -> {p.ctl:.1f}) en semaine normale"
            )
        prev_ctl = p.ctl

    race_tsb_str = f" → TSB course={race_tsb:+.1f} (+{rest_days}j)" if rest_days > 0 else ""
    summary = (
        f"CTL {initial_ctl:.1f} -> pic S{peak_week}:{peak_ctl:.1f} -> "
        f"finale:{final_ctl:.1f} | TSB final={final_tsb:+.1f}{race_tsb_str}"
    )

    if issues:
        score = max(0.0, 1.0 - len(issues) * 0.35)
        return EvalResult(False, score, name, summary, issues, fixes)

    return EvalResult(True, 1.0, name, summary + " [OK]", [], [])


# ─── Critère 2 : Semaines de récupération — qualité pour profils avancés ──────

def check_recovery_week_quality(
    plan: TrainingPlanSchema,
    profile: AthleteProfileSchema,
) -> EvalResult:
    """
    Pour un athlète avancé, une semaine de récupération doit :
    - Maintenir un TSS ≥ 70 % du volume hebdo de départ (éviter le désentraînement)
    - Contenir au moins 1 session avec une stimulation neuromusculaire brève
      (sprints courts, accélérations) pour ne pas perdre le "punch"
    """
    name = "Récup : qualité et contenu"

    recovery_weeks = [w for w in plan.weeks if w.is_recovery_week]
    if not recovery_weeks:
        return EvalResult(True, 1.0, name, "Aucune semaine de récup dans le plan", [], [])

    issues: list[str] = []
    fixes: list[str] = []
    initial_tss = plan.initial_weekly_tss
    is_advanced = profile.level in ("advanced", "expert")

    for week in recovery_weeks:
        # Plancher TSS
        tss_floor = initial_tss * 0.70
        if week.total_tss_target < tss_floor:
            issues.append(
                f"S{week.week_number} RECUP ({week.phase.upper()}): "
                f"TSS={week.total_tss_target:.0f} < plancher {tss_floor:.0f} "
                f"(70% du volume de départ {initial_tss:.0f})"
            )
            fixes.append(
                "Relever le plafond TSS recovery : changer 0.73 → 0.82 "
                "dans compute_weekly_tss_targets() de periodization.py"
            )

        # Contenu neuromusculaire pour avancés
        if is_advanced:
            has_quality = any(
                s.zone_code not in ("Z1", "Z2") or "sprint" in s.description_fr.lower()
                for s in week.sessions
            )
            all_pure_recovery = all(
                s.workout_type == "recovery" and s.zone_code in ("Z1", "Z2")
                for s in week.sessions
            )
            if all_pure_recovery:
                issues.append(
                    f"S{week.week_number} RECUP ({week.phase.upper()}): "
                    "100% récupération Z1/Z2 — un athlète avancé perd ses adaptations "
                    "neuromusculaires rapides en 72-96h sans stimulus"
                )
                fixes.append(
                    "Ajouter 1 session d'activation neuromusculaire en milieu de récup : "
                    "'3×30'' sprint max + 3min récup Z1' (~30min, TSS≈25). "
                    "Modifier _build_sessions() recovery block dans plan_builder.py"
                )

    if issues:
        score = max(0.0, 1.0 - len(issues) * 0.30)
        return EvalResult(
            False, score, name,
            f"{len(recovery_weeks)} semaine(s) de récup — qualité insuffisante",
            issues, fixes,
        )

    return EvalResult(
        True, 1.0, name,
        f"{len(recovery_weeks)} semaine(s) de récup correctement dosées",
        [], [],
    )


# ─── Critère 3 : Spécificité cyclosportive — blocs Z4 longs ───────────────────

def check_cyclosportive_z4_specificity(
    plan: TrainingPlanSchema,
    profile: AthleteProfileSchema,
) -> EvalResult:
    """
    Une cyclosportive = longs cols au seuil (Z4, 100% FTP).
    Le plan doit comporter en build/peak :
    - ≥ 3 séances d'intervalles Z4 (seuil lactique)
    - Chaque séance ≥ 24 min de travail en zone (ex: 2×12, 3×10, 2×20, 3×15)
    - Ratio Z4 ≥ 35% des séances d'intervalles en build+peak
    """
    name = "Cyclosportive : spécificité Z4 (seuil)"

    if profile.objective.type not in ("cyclosportive", "etape_du_tour"):
        return EvalResult(True, 1.0, name, "Objectif non-cyclosportive — skip", [], [])

    z4_sessions: list[tuple[int, str]] = []   # (week_number, description)
    total_intensity = 0

    for week in plan.weeks:
        if week.phase in ("base", "taper"):
            continue
        for s in week.sessions:
            if s.workout_type != "intervals":
                continue
            total_intensity += 1
            if s.zone_code == "Z4":
                z4_sessions.append((week.week_number, s.description_fr))

    issues: list[str] = []
    fixes: list[str] = []

    if len(z4_sessions) < 3:
        issues.append(
            f"Seulement {len(z4_sessions)} séance(s) Z4 en build/peak "
            f"(minimum 3 pour une cyclosportive)"
        )
        fixes.append(
            "Abaisser le seuil Z4 dans _build_week_template() (build phase) : "
            "changer 'phase_progress >= 0.35' → 'phase_progress >= 0.20' "
            "pour introduire le travail au seuil dès la 2e semaine de build"
        )

    if total_intensity > 0:
        ratio = len(z4_sessions) / total_intensity
        if ratio < 0.35:
            issues.append(
                f"Ratio Z4/(total intensité build+peak) = {ratio:.0%} < 35% requis. "
                f"Z4={len(z4_sessions)}, total={total_intensity} séances intenses"
            )
            fixes.append(
                "Ajouter 3×15min dans THRESHOLD_STRUCTURES pour plus de variété "
                "et augmenter le poids des séances seuil vs VO2"
            )

    # Vérifier les sorties longues en build/peak
    lr_without_z4 = []
    for week in plan.weeks:
        if week.phase not in ("build", "peak"):
            continue
        for s in week.sessions:
            if s.workout_type == "long_ride":
                desc = s.description_fr.lower()
                has_z4 = any(kw in desc for kw in ["z4", "seuil", "col", "montée", "threshold"])
                if not has_z4:
                    lr_without_z4.append(f"S{week.week_number}")
    if lr_without_z4:
        issues.append(
            f"Sorties longues sans mention Z4/seuil en build/peak : {', '.join(lr_without_z4)}. "
            "Une cyclosportive = simuler des cols au seuil en fin de sortie longue"
        )
        fixes.append(
            "Modifier la description long_ride en build/peak : "
            "remplacer 'blocs tempo Z3 optionnels' par "
            "'blocs au seuil Z4 (2×10-15min simulant une montée de col)'"
        )

    if issues:
        score = max(0.0, 1.0 - len(issues) * 0.30)
        return EvalResult(
            False, score, name,
            f"{len(z4_sessions)} séances Z4 sur {total_intensity} intenses "
            f"en build+peak",
            issues, fixes,
        )

    return EvalResult(
        True, 1.0, name,
        f"{len(z4_sessions)}/{total_intensity} séances Z4 en build+peak "
        f"({len(z4_sessions)/max(total_intensity,1):.0%}) — spécificité OK",
        [], [],
    )


# ─── Critère 4 : Équilibre Z3/Z4/Z5 en build+peak ────────────────────────────

def check_zone_balance(plan: TrainingPlanSchema) -> EvalResult:
    """
    Trop de Z3 (Sweet Spot / Tempo) par rapport à Z4 (Seuil) est sous-optimal
    pour une cyclosportive. Le travail Z3 est utile en base, mais en build/peak
    il faut progressivement basculer vers Z4 + Z5.
    Règle : Z3 ne doit pas excéder 2× le nombre de séances Z4 en build+peak.
    """
    name = "Distribution zones Z3/Z4/Z5 (build+peak)"

    counts = {"Z3": 0, "Z4": 0, "Z5": 0}
    for week in plan.weeks:
        if week.phase not in ("build", "peak"):
            continue
        for s in week.sessions:
            if s.workout_type == "intervals" and s.zone_code in counts:
                counts[s.zone_code] += 1

    total = sum(counts.values())
    if total == 0:
        return EvalResult(True, 1.0, name, "Aucun intervalle en build+peak", [], [])

    issues: list[str] = []
    fixes: list[str] = []

    if counts["Z3"] > counts["Z4"] * 2:
        issues.append(
            f"Z3={counts['Z3']} >> Z4={counts['Z4']} : trop de Sweet Spot/Tempo. "
            "En phase build, le seuil (Z4) devrait prendre le dessus sur le Tempo (Z3)"
        )
        fixes.append(
            "Abaisser le seuil d'activation Z4 dans _build_week_template() : "
            "'phase_progress >= 0.20' au lieu de 0.35"
        )

    summary = " | ".join(f"Z{z[-1]}={v}" for z, v in counts.items())
    if issues:
        return EvalResult(
            False, 0.5, name,
            f"Distribution déséquilibrée ({summary})",
            issues, fixes,
        )

    return EvalResult(True, 1.0, name, f"Distribution équilibrée ({summary})", [], [])


# ─── Critère 5 : Nombre de semaines de récup dans le plan ────────────────────

def check_recovery_frequency(plan: TrainingPlanSchema) -> EvalResult:
    """
    Un plan de 10-12 semaines avec 3 semaines de récup + taper = 40% de "décharge".
    Règle : le ratio semaines de récup / semaines totales ne doit pas dépasser 25%.
    """
    name = "Fréquence des semaines de récup"

    recovery_weeks = [w for w in plan.weeks if w.is_recovery_week]
    total = plan.weeks_count
    ratio = len(recovery_weeks) / total if total > 0 else 0

    issues: list[str] = []
    fixes: list[str] = []

    if ratio > 0.25:
        issues.append(
            f"{len(recovery_weeks)} semaines de récup sur {total} "
            f"= {ratio:.0%} > 25% recommandé. "
            f"Semaines concernées : {[w.week_number for w in recovery_weeks]}"
        )
        fixes.append(
            "Passer au pattern 4+1 pour les plans ≤ 12 semaines : "
            "modifier _recovery_every_n() dans periodization.py → return 4 if weeks_total <= 12"
        )
        # Vérifier si peak est touché
        peak_recovery = [w for w in recovery_weeks if w.phase == "peak"]
        if peak_recovery:
            issues.append(
                f"CRITIQUE : la phase PEAK (S{peak_recovery[0].week_number}) est marquée RECOVERY. "
                "Le point culminant du plan est transformé en décharge !"
            )
            fixes.insert(0,
                "CORRECTION URGENTE : exclure 'peak' de la condition is_recovery. "
                "Dans periodization.py, compute_weekly_tss_targets() : "
                "changer 'phase.phase != taper' → 'phase.phase not in (taper, peak)'"
            )

    if issues:
        return EvalResult(
            False, max(0.0, 1.0 - ratio * 2), name,
            f"{len(recovery_weeks)}/{total} semaines = {ratio:.0%} de décharge",
            issues, fixes,
        )

    return EvalResult(
        True, 1.0, name,
        f"{len(recovery_weeks)}/{total} semaines de récup ({ratio:.0%}) — OK",
        [], [],
    )


# ─── Rapport d'évaluation ─────────────────────────────────────────────────────

CRITERIA = [
    ("ctl",          check_ctl_stability),
    ("recovery",     check_recovery_week_quality),
    ("z4",           check_cyclosportive_z4_specificity),
    ("zones",        check_zone_balance),
    ("freq_recup",   check_recovery_frequency),
]


def _run_criterion(name, fn, plan, profile, projection) -> EvalResult:
    """Dispatch — chaque fonction n'accepte que les args qu'elle connaît."""
    import inspect
    sig = inspect.signature(fn)
    kwargs = {}
    if "plan" in sig.parameters:
        kwargs["plan"] = plan
    if "profile" in sig.parameters:
        kwargs["profile"] = profile
    if "projection" in sig.parameters:
        kwargs["projection"] = projection
    return fn(**kwargs)


def evaluate_plan(
    plan: TrainingPlanSchema,
    profile: AthleteProfileSchema,
    projection: list[FitnessProjectionPoint] | None,
    verbose: bool = False,
) -> tuple[float, list[EvalResult]]:

    SEP = "=" * 72
    DASH = "-" * 72

    print()
    print(SEP)
    print("  EVALUATION QUALITE DU PLAN — Banister")
    print(SEP)

    initial_ctl = profile.current_ctl
    if initial_ctl and projection:
        final_ctl = projection[-1].ctl
        peak_ctl = max(p.ctl for p in projection)
        print(f"  Profil  : {profile.level} | {profile.availability.hours_per_week}h/sem"
              f" | {profile.objective.type}")
        print(f"  CTL     : initiale={initial_ctl:.1f} → finale={final_ctl:.1f}"
              f" (pic={peak_ctl:.1f})")
    print(f"  Plan    : {plan.weeks_count} semaines | TSS {plan.initial_weekly_tss:.0f}"
          f" → {plan.peak_weekly_tss:.0f} TSS/sem")
    print()

    results: list[EvalResult] = []
    for key, fn in CRITERIA:
        r = _run_criterion(key, fn, plan, profile, projection)
        results.append(r)

        status = "PASS" if r.passed else "FAIL"
        bar = "[OK]" if r.passed else "[!!]"
        print(f"  {bar} {r.name}")
        print(f"       {r.message}")

        if not r.passed:
            for issue in r.issues:
                print(f"       >> PROBLEME : {issue}")
            if verbose:
                for fix in r.fixes:
                    print(f"       >> CORRECTION : {fix}")
        print()

    # ── Score global ───────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    score = sum(r.score for r in results) / total

    print(DASH)
    print(f"  Score global : {score:.0%}  ({passed}/{total} criteres OK)")

    if score >= 0.85:
        verdict = "Plan de qualite — pret pour production"
    elif score >= 0.65:
        verdict = "Plan acceptable — corrections recommandees"
    else:
        verdict = "Plan insuffisant — corrections urgentes avant utilisation"

    print(f"  Verdict : {verdict}")
    print()

    if not all(r.passed for r in results):
        print("  CORRECTIONS NECESSAIRES :")
        for r in results:
            if not r.passed:
                for fix in r.fixes:
                    print(f"  • [{r.name}] {fix}")
        print()

    return score, results


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Évalue la qualité d'un plan Banister",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--profile", "-p", required=True,
                        help="Fichier JSON du profil athlète")
    parser.add_argument("--start-date", "-d", metavar="YYYY-MM-DD",
                        help="Date de début du plan")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Affiche les suggestions de correction pour chaque problème")
    args = parser.parse_args()

    profile_path = Path(args.profile)
    if not profile_path.exists():
        print(f"Erreur : fichier introuvable — {profile_path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    try:
        profile = AthleteProfileSchema.model_validate(raw)
    except Exception as e:
        print(f"Profil invalide : {e}", file=sys.stderr)
        sys.exit(1)

    start_date = None
    if args.start_date:
        start_date = date.fromisoformat(args.start_date)

    print(f"\n-> Generation du plan depuis {profile_path.name}...")
    plan = generate_plan(profile, start_date=start_date)

    projection = None
    if profile.current_ctl is not None:
        projection = project_fitness_from_plan(
            plan,
            initial_ctl=profile.current_ctl,
            initial_atl=profile.current_atl or 0.0,
        )

    score, results = evaluate_plan(plan, profile, projection, verbose=args.verbose)

    # Exit code non-zéro si le plan échoue
    sys.exit(0 if score >= 0.85 else 1)


if __name__ == "__main__":
    main()
