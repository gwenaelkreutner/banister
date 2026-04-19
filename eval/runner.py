"""CLI principal du système d'évaluation offline Banister.

Usage :
  python -m eval.runner                            # tout, avec LLM
  python -m eval.runner --profiles matrix          # matrice systématique seulement
  python -m eval.runner --profiles edge_cases      # edge cases seulement
  python -m eval.runner --skip-llm                 # règles déterministes uniquement
  python -m eval.runner --skip-rules               # LLM uniquement
  python -m eval.runner --output path/to/out.json  # chemin de sortie custom
  python -m eval.runner --verbose                  # affiche chaque éval en temps réel
  python -m eval.runner --max-concurrent 5         # parallélisme LLM (défaut : 3)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import UTC, date, datetime, timedelta

# Force UTF-8 output on Windows (évite les UnicodeEncodeError avec cp1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

import yaml

from app.engine.plan_builder import generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from eval.aggregator import aggregate
from eval.config import LLM_MAX_CONCURRENT
from eval.rules.checker import check_all

_PROFILES_DIR = Path(__file__).parent / "profiles"
_RESULTS_DIR = Path(__file__).parent / "results"


# ─────────────────────────────────────────────────────────────────────────────
# Chargement des profils YAML
# ─────────────────────────────────────────────────────────────────────────────

def _load_yaml_profiles(source: str) -> list[dict]:
    """Charge les profils depuis un ou plusieurs fichiers YAML."""
    if source == "all":
        files = [_PROFILES_DIR / "matrix.yaml", _PROFILES_DIR / "edge_cases.yaml"]
    elif source == "matrix":
        files = [_PROFILES_DIR / "matrix.yaml"]
    elif source == "edge_cases":
        files = [_PROFILES_DIR / "edge_cases.yaml"]
    else:
        files = [Path(source)]

    raw_profiles: list[dict] = []
    for f in files:
        if not f.exists():
            print(f"[ERREUR] Fichier introuvable : {f}", file=sys.stderr)
            sys.exit(1)
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        raw_profiles.extend(data.get("profiles", []))

    return raw_profiles


def _profile_from_dict(raw: dict) -> tuple[str, AthleteProfileSchema]:
    """Convertit un dict YAML en (profile_id, AthleteProfileSchema)."""
    profile_id = raw["id"]

    # Conversion target_weeks → target_date
    target_weeks = raw.get("target_weeks")
    target_date = date.today() + timedelta(weeks=int(target_weeks)) if target_weeks else None

    objective = ObjectiveProfile(type=raw["objective_type"], target_date=target_date)
    availability = AvailabilityProfile(
        hours_per_week=raw["hours_per_week"],
        preferred_days=raw["preferred_days"],
    )
    equipment = EquipmentProfile(
        power_meter=raw["power_meter"],
        ftp=raw.get("ftp"),
        ftp_source=raw.get("ftp_source", "estimated"),
    )
    physio = PhysioProfile(
        age=raw["age"],
        hr_max=raw["hr_max"],
        hr_max_source=raw.get("hr_max_source", "estimated"),
        hr_rest=raw["hr_rest"],
        hr_rest_source=raw.get("hr_rest_source", "declared"),
    )
    profile = AthleteProfileSchema(
        objective=objective,
        availability=availability,
        equipment=equipment,
        physio=physio,
        level=raw["level"],
        structured_plan_history=raw["structured_plan_history"],
        coaching_mode=raw["coaching_mode"],
        health_constraints=raw["health_constraints"],
        injury_status=raw.get("injury_status"),
        sex=raw.get("sex"),
        weight_kg=raw.get("weight_kg"),
    )
    return profile_id, profile


# ─────────────────────────────────────────────────────────────────────────────
# Construction d'une évaluation individuelle
# ─────────────────────────────────────────────────────────────────────────────

def _plan_meta(plan) -> dict:
    """Extrait les métadonnées clés du plan."""
    return {
        "weeks_count": plan.weeks_count,
        "initial_weekly_tss": round(plan.initial_weekly_tss, 1),
        "peak_weekly_tss": round(plan.peak_weekly_tss, 1),
        "coaching_mode": plan.coaching_mode,
        "phases": [w.phase for w in plan.weeks],
        "recovery_weeks": [w.week_number for w in plan.weeks if w.is_recovery_week],
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
    }


def build_evaluation(
    profile_id: str,
    raw_profile: dict,
    profile: AthleteProfileSchema,
    plan,
    check_result,
    llm_eval,
) -> dict:
    """Construit le dict complet d'une évaluation (pour le rapport JSON final)."""
    final_score = None
    has_llm_error = False
    if llm_eval is not None:
        if llm_eval.error:
            has_llm_error = True
        else:
            final_score = llm_eval.composite_score

    return {
        "profile_id": profile_id,
        "description": raw_profile.get("description", ""),
        "profile": profile.model_dump(mode="json"),
        "plan_meta": _plan_meta(plan),
        "deterministic_checks": check_result.to_dict() if check_result else None,
        "llm_evaluation": llm_eval.to_dict() if llm_eval else None,
        "final_score": final_score,
        "has_critical_violations": (
            check_result.critical_count > 0 if check_result else False
        ),
        "llm_error": has_llm_error,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrateur principal
# ─────────────────────────────────────────────────────────────────────────────

async def _run(args: argparse.Namespace) -> None:
    raw_profiles = _load_yaml_profiles(args.profiles)
    total = len(raw_profiles)
    print(f"[eval] {total} profil(s) chargé(s) — source : {args.profiles}")

    # Génération des plans (synchrone, rapide)
    loaded: list[tuple[str, dict, AthleteProfileSchema, object]] = []
    for raw in raw_profiles:
        try:
            profile_id, profile = _profile_from_dict(raw)
            plan = generate_plan(profile)
            loaded.append((profile_id, raw, profile, plan))
        except Exception as exc:
            print(f"  [SKIP] {raw.get('id', '?')} — erreur génération plan : {exc}")

    print(f"[eval] {len(loaded)} plan(s) générés avec succès")

    # Vérifications déterministes
    check_results: dict[str, object] = {}
    if not args.skip_rules:
        print("[eval] Vérifications déterministes en cours...")
        for profile_id, _, profile, plan in loaded:
            check_results[profile_id] = check_all(profile, plan)
        violations_total = sum(len(cr.violations) for cr in check_results.values())
        print(f"  → {violations_total} violation(s) détectée(s)")
    else:
        print("[eval] Verifications deterministes ignorees (--skip-rules)")

    # Évaluations LLM
    llm_results: dict[str, object] = {}
    if not args.skip_llm:
        from eval.llm.judge import evaluate

        semaphore = asyncio.Semaphore(args.max_concurrent)
        print(f"[eval] Évaluations LLM en cours ({args.max_concurrent} max en parallèle)...")

        async def _eval_one(profile_id, profile, plan):
            try:
                result = await evaluate(profile, plan, semaphore=semaphore)
                if args.verbose:
                    status = f"✓ score={result.composite_score:.1f}" if not result.error else f"✗ {result.error}"
                    print(f"  [{profile_id}] {status}")
                return profile_id, result
            except Exception as exc:
                if args.verbose:
                    print(f"  [{profile_id}] ✗ Exception inattendue : {exc}")
                return profile_id, None

        tasks = [_eval_one(pid, p, pl) for pid, _, p, pl in loaded]
        results = await asyncio.gather(*tasks)
        llm_results = {pid: res for pid, res in results if res is not None}
        errors = sum(1 for res in llm_results.values() if res.error)
        print(f"  → {len(llm_results)} évaluations LLM ({errors} erreur(s))")
    else:
        print("[eval] Évaluations LLM ignorées (--skip-llm)")

    # Construction des évaluations finales
    evaluations = []
    for profile_id, raw, profile, plan in loaded:
        check_result = check_results.get(profile_id)
        llm_eval = llm_results.get(profile_id)
        evaluations.append(
            build_evaluation(profile_id, raw, profile, plan, check_result, llm_eval)
        )

    # Agrégation
    report = aggregate(evaluations)
    report["generated_at"] = datetime.now(UTC).isoformat() + "Z"
    report["engine"] = "banister"

    # Sauvegarde
    output_path = Path(args.output) if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)

    _print_summary(report, output_path)


def _default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    return _RESULTS_DIR / f"eval_{timestamp}.json"


def _print_summary(report: dict, output_path: Path) -> None:
    s = report.get("summary", {})
    print("\n" + "─" * 60)
    print("RÉSUMÉ")
    print("─" * 60)
    print(f"  Profils évalués     : {s.get('total_profiles', 0)}")
    print(f"  Évalués par LLM     : {s.get('llm_evaluated', 0)}")
    if s.get("mean_composite_score") is not None:
        print(f"  Score moyen         : {s['mean_composite_score']:.2f} / 10")
        print(f"  Écart-type          : {s['std_composite_score']:.2f}")
    print(f"  Plans critiques     : {s.get('critical_plans_pct', 0):.1f}%")
    print(f"  Violations déterm.  : {s.get('deterministic_violations_total', 0)}")
    print(f"  Plans avec critical : {s.get('plans_with_critical_violations', 0)}")

    clusters = s.get("error_clusters", [])
    if clusters:
        print("\n  TOP CLUSTERS D'ERREURS :")
        for c in clusters[:5]:
            if c["type"] == "deterministic":
                print(f"    [{c['severity']}] {c['rule']} → {c['count']} plan(s)")
            else:
                print(f"    [llm] {c['dimension']} < {c['threshold']} → {c['count']} plan(s)")

    print(f"\n  Rapport sauvegardé : {output_path}")
    print("─" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Évaluation offline des plans d'entraînement Banister",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profiles",
        default="all",
        help="Source des profils : 'all' (défaut), 'matrix', 'edge_cases', ou chemin YAML",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Ignorer l'évaluation LLM (règles déterministes uniquement)",
    )
    parser.add_argument(
        "--skip-rules",
        action="store_true",
        help="Ignorer les vérifications déterministes (LLM uniquement)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Chemin de sortie du rapport JSON (défaut : eval/results/eval_<timestamp>.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche l'avancement de chaque évaluation",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=LLM_MAX_CONCURRENT,
        help=f"Nombre max d'appels LLM en parallèle (défaut : {LLM_MAX_CONCURRENT})",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
