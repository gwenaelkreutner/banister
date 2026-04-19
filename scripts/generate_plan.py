#!/usr/bin/env python3
"""
Génère un plan d'entraînement CycloCoach depuis un profil athlétique JSON.

Le JSON d'entrée correspond au champ `profile` de la table `athlete_profiles`
(schéma : AthleteProfileSchema). Récupérable depuis Supabase :
    SELECT profile FROM athlete_profiles WHERE user_id = <id>;

Usage :
    python scripts/generate_plan.py --profile scripts/sample_profile.json
    python scripts/generate_plan.py --profile scripts/sample_profile.json --json out.json
    python scripts/generate_plan.py --sample          # affiche un profil exemple
    python scripts/generate_plan.py --sample > scripts/mon_profil.json  # crée un fichier
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# UTF-8 sur les terminaux Windows (cp1252 par défaut sinon)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Rendre les imports app disponibles depuis n'importe où
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.atl_ctl import FitnessProjectionPoint, project_fitness_from_plan, tsb_label
from app.engine.plan_builder import generate_plan
from app.engine.schemas import AthleteProfileSchema

# -- Profil exemple ------------------------------------------------------------

SAMPLE_PROFILE: dict = {
    "objective": {
        "type": "cyclosportive",
        "target_date": "2026-09-20"
    },
    "availability": {
        "hours_per_week": 8,
        "preferred_days": ["tuesday", "thursday", "saturday", "sunday"]
    },
    "level": "intermediate",
    "structured_plan_history": True,
    "equipment": {
        "power_meter": False,
        "ftp": None,
        "ftp_source": "estimated"
    },
    "physio": {
        "age": 35,
        "hr_max": 185,
        "hr_max_source": "declared",
        "hr_rest": 55,
        "hr_rest_source": "declared"
    },
    "coaching_mode": "hr",
    "health_constraints": False,
    "current_ctl": 52.0,
    "current_atl": 48.0,
    "current_tsb": 4.0
}

# -- Helpers d'affichage -------------------------------------------------------

_DAY_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]

_PHASE_LABEL = {
    "base":  "BASE ",
    "build": "BUILD",
    "peak":  "PEAK ",
    "taper": "TAPER",
}

_TYPE_SHORT = {
    "long_ride": "sortie longue",
    "intervals":  "intervalles  ",
    "endurance":  "endurance    ",
    "recovery":   "recuperation ",
}


def _session_line(session, prefix: str) -> str:
    day = _DAY_FR[session.day_of_week]
    stype = _TYPE_SHORT.get(session.workout_type, session.workout_type)
    desc = session.description_fr
    # Garder seulement la partie après " — " si présente (le détail de structure)
    detail = ""
    if " — " in desc:
        detail = "  [" + desc.split(" — ", 1)[1] + "]"
    return (
        f"    {prefix} {day}  {stype}  {session.zone_code}"
        f"  {session.duration_minutes:3d}min"
        f"  {session.tss_target:5.1f} TSS{detail}"
    )


def _tsb_tag(tsb: float) -> str:
    """Tag court pour le TSB (ASCII, compatible Windows)."""
    if tsb < -30: return "!SURNMG"
    if tsb < 0:   return "fatigue"
    if tsb <= 5:  return "forme  "
    if tsb <= 15: return "POINTE "
    if tsb <= 20: return "frais  "
    return "transit"


# -- Affichage principal -------------------------------------------------------

def print_summary(
    plan,
    profile: AthleteProfileSchema,
    projection: list[FitnessProjectionPoint] | None,
) -> None:
    has_strava = profile.current_ctl is not None
    ctl_str = (
        f"CTL={profile.current_ctl:.1f}  ATL={profile.current_atl:.1f}  TSB={profile.current_tsb:+.1f}"
        if has_strava else "pas de données Strava"
    )

    sep = "=" * 70
    print()
    print(sep)
    print("  Banister -- Generation de plan d'entrainement")
    print(sep)
    print(f"  Profil   : {profile.level} | {profile.availability.hours_per_week}h/sem"
          f" | mode {profile.coaching_mode.upper()}")
    print(f"  Forme    : {ctl_str}")
    print(f"  Objectif : {profile.objective.type}"
          + (f" - {profile.objective.target_date}" if profile.objective.target_date else ""))
    print(f"  Plan     : {plan.weeks_count} semaines"
          + (f"  ({plan.start_date} -> {plan.end_date})" if plan.start_date else ""))
    print(f"  Charge   : {plan.initial_weekly_tss:.0f} TSS/sem (depart)"
          f" -> {plan.peak_weekly_tss:.0f} TSS/sem (pic)")
    print()

    # Index projection : dernier point de chaque semaine
    proj_by_week: dict[int, FitnessProjectionPoint] = {}
    if projection:
        for p in projection:
            proj_by_week[p.week_number] = p  # dernier point = fin de semaine

    # En-tête du tableau
    if has_strava:
        print(f"  {'SEM':>3}  {'PHASE':<5}  {'TSS':>6}  {'CTL':>5}  {'TSB':>5}  {'':5}")
        print("  " + "-" * 40)
    else:
        print(f"  {'SEM':>3}  {'PHASE':<5}  {'TSS':>6}  {'':5}")
        print("  " + "-" * 25)

    for week in plan.weeks:
        tag = "RECUP" if week.is_recovery_week else "     "
        sessions = sorted(week.sessions, key=lambda s: s.day_of_week)

        if has_strava and week.week_number in proj_by_week:
            p = proj_by_week[week.week_number]
            tsb_emoji = _tsb_tag(p.tsb)
            print(
                f"  {week.week_number:>3}  {_PHASE_LABEL[week.phase]}"
                f"  {week.total_tss_target:>6.1f}"
                f"  {p.ctl:>5.1f}  {p.tsb:>+5.1f}  {tag}"
            )
        else:
            print(
                f"  {week.week_number:>3}  {_PHASE_LABEL[week.phase]}"
                f"  {week.total_tss_target:>6.1f}"
                f"  {tag}"
            )

        # Sessions
        for i, session in enumerate(sessions):
            prefix = "`" if i == len(sessions) - 1 else "+"
            print(_session_line(session, prefix))

        print()

    # Résumé fin de plan
    if projection:
        last = projection[-1]
        print(f"  Fin de plan : CTL={last.ctl:.1f}  ATL={last.atl:.1f}"
              f"  TSB={last.tsb:+.1f}  {_tsb_text(last.tsb)}")
    print()


# -- Point d'entrée ------------------------------------------------------------

def _tsb_text(tsb: float) -> str:
    """Version ASCII du label TSB (compatible tous les terminaux)."""
    if tsb < -30:
        return "[SURMENAGE]"
    if tsb < 0:
        return "[Fatigue normale]"
    if tsb <= 5:
        return "[Bonne forme]"
    if tsb <= 15:
        return "[Forme de pointe]"
    if tsb <= 20:
        return "[Tres frais]"
    return "[Transition]"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère un plan CycloCoach depuis un profil athlète JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profile", "-p",
        help="Fichier JSON du profil athlète (AthleteProfileSchema)",
    )
    parser.add_argument(
        "--json", "-j",
        metavar="OUTPUT",
        help="Sauvegarde le plan complet en JSON (pour debug ou import DB)",
    )
    parser.add_argument(
        "--sample", "-s",
        action="store_true",
        help="Affiche un profil exemple (AthleteProfileSchema) et quitte",
    )
    parser.add_argument(
        "--start-date", "-d",
        metavar="YYYY-MM-DD",
        help="Date de debut du plan (defaut : aujourd'hui)",
    )
    args = parser.parse_args()

    # Mode --sample
    if args.sample:
        print(json.dumps(SAMPLE_PROFILE, indent=2, ensure_ascii=False))
        return

    if not args.profile:
        parser.error("--profile requis  (ou --sample pour voir un exemple)")

    # Chargement du profil
    profile_path = Path(args.profile)
    if not profile_path.exists():
        print(f"Erreur : fichier introuvable — {profile_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(profile_path, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Erreur JSON : {e}", file=sys.stderr)
        sys.exit(1)

    try:
        profile = AthleteProfileSchema.model_validate(raw)
    except Exception as e:
        print(f"Profil invalide (AthleteProfileSchema) :\n{e}", file=sys.stderr)
        sys.exit(1)

    # Parse de la date de début (optionnelle)
    start_date = None
    if args.start_date:
        try:
            start_date = date.fromisoformat(args.start_date)
        except ValueError:
            print(f"Erreur : date invalide '{args.start_date}' (format attendu : YYYY-MM-DD)", file=sys.stderr)
            sys.exit(1)

    # Génération du plan
    plan = generate_plan(profile, start_date=start_date)

    # Projection CTL (si CTL Strava disponible)
    projection: list[FitnessProjectionPoint] | None = None
    if profile.current_ctl is not None:
        projection = project_fitness_from_plan(
            plan,
            initial_ctl=profile.current_ctl,
            initial_atl=profile.current_atl or 0.0,
        )

    # Affichage
    print_summary(plan, profile, projection)

    # Sauvegarde JSON optionnelle
    if args.json:
        out_path = Path(args.json)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                plan.model_dump(mode="json"),
                f,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        print(f"Plan JSON sauvegardé : {out_path}")


if __name__ == "__main__":
    main()
