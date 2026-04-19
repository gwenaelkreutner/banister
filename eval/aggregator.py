"""Agrégation des évaluations individuelles en rapport de synthèse.

Produit :
- Score moyen et écart-type
- Distribution des scores composites (4 buckets)
- Pourcentage de plans problématiques
- Clusters d'erreurs : violations déterministes + dimensions LLM faibles
"""

import math
import statistics
from typing import Any

from eval.config import (
    COMPOSITE_WEIGHTS,
    LOW_DIMENSION_THRESHOLD,
    SCORE_ACCEPTABLE,
    SCORE_CONCERNING,
    SCORE_CRITICAL,
)

_SCORE_DIMENSIONS = list(COMPOSITE_WEIGHTS.keys())


def aggregate(evaluations: list[dict]) -> dict:
    """Construit le rapport final à partir de la liste d'évaluations individuelles.

    Args:
        evaluations: Liste de dicts produits par build_evaluation() dans runner.py.

    Returns:
        Rapport complet avec summary + liste complète des évaluations.
    """
    if not evaluations:
        return {"summary": {}, "evaluations": []}

    composite_scores = [
        e["llm_evaluation"]["composite_score"]
        for e in evaluations
        if e.get("llm_evaluation") and e["llm_evaluation"].get("composite_score") is not None
        and not e["llm_evaluation"].get("error")
    ]

    summary = _build_summary(evaluations, composite_scores)

    return {
        "summary": summary,
        "evaluations": evaluations,
    }


def _build_summary(evaluations: list[dict], composite_scores: list[float]) -> dict:
    total = len(evaluations)
    llm_evaluated = len(composite_scores)

    # ── Statistiques des scores composites ───────────────────────────────────
    if composite_scores:
        mean_score = round(statistics.mean(composite_scores), 2)
        std_score = round(statistics.stdev(composite_scores), 2) if len(composite_scores) > 1 else 0.0
    else:
        mean_score = None
        std_score = None

    distribution = _score_distribution(composite_scores)

    critical_count = sum(1 for s in composite_scores if s < SCORE_CRITICAL)
    critical_pct = round(critical_count / total * 100, 1) if total > 0 else 0.0

    # ── Statistiques par dimension LLM ────────────────────────────────────────
    by_dimension = _dimension_stats(evaluations)

    # ── Clusters d'erreurs ────────────────────────────────────────────────────
    error_clusters = _build_error_clusters(evaluations)

    # ── Violations déterministes ──────────────────────────────────────────────
    all_violations = [
        v
        for e in evaluations
        for v in (e.get("deterministic_checks") or {}).get("violations", [])
    ]
    plans_with_critical = sum(
        1
        for e in evaluations
        if (e.get("deterministic_checks") or {}).get("critical_count", 0) > 0
    )

    return {
        "total_profiles": total,
        "llm_evaluated": llm_evaluated,
        "mean_composite_score": mean_score,
        "std_composite_score": std_score,
        "score_distribution": distribution,
        "critical_plans_pct": critical_pct,
        "by_dimension": by_dimension,
        "error_clusters": error_clusters,
        "deterministic_violations_total": len(all_violations),
        "plans_with_critical_violations": plans_with_critical,
    }


def _score_distribution(scores: list[float]) -> dict:
    """Répartit les scores dans 4 buckets de qualité."""
    buckets: dict[str, int] = {
        f"critical (0-{SCORE_CRITICAL:.0f})": 0,
        f"concerning ({SCORE_CRITICAL:.0f}-{SCORE_CONCERNING:.0f})": 0,
        f"acceptable ({SCORE_CONCERNING:.0f}-{SCORE_ACCEPTABLE:.0f})": 0,
        f"excellent ({SCORE_ACCEPTABLE:.0f}-10)": 0,
    }
    keys = list(buckets.keys())
    for s in scores:
        if s < SCORE_CRITICAL:
            buckets[keys[0]] += 1
        elif s < SCORE_CONCERNING:
            buckets[keys[1]] += 1
        elif s < SCORE_ACCEPTABLE:
            buckets[keys[2]] += 1
        else:
            buckets[keys[3]] += 1
    return buckets


def _dimension_stats(evaluations: list[dict]) -> dict[str, dict]:
    """Calcule mean/min/max pour chaque dimension LLM."""
    dim_scores: dict[str, list[float]] = {dim: [] for dim in _SCORE_DIMENSIONS}

    for e in evaluations:
        llm = e.get("llm_evaluation")
        if not llm or llm.get("error"):
            continue
        for dim in _SCORE_DIMENSIONS:
            score = llm.get("scores", {}).get(dim)
            if score is not None:
                dim_scores[dim].append(float(score))

    result = {}
    for dim, scores in dim_scores.items():
        if scores:
            result[dim] = {
                "mean": round(statistics.mean(scores), 2),
                "min": min(scores),
                "max": max(scores),
                "low_count": sum(1 for s in scores if s < LOW_DIMENSION_THRESHOLD),
            }
        else:
            result[dim] = {"mean": None, "min": None, "max": None, "low_count": 0}
    return result


def _build_error_clusters(evaluations: list[dict]) -> list[dict[str, Any]]:
    """Identifie les clusters d'erreurs récurrentes.

    Deux types de clusters :
    1. Violations déterministes groupées par rule_id
    2. Dimensions LLM systématiquement basses (< LOW_DIMENSION_THRESHOLD)
    """
    clusters: list[dict] = []

    # ── Cluster 1 : violations déterministes par rule_id ─────────────────────
    rule_buckets: dict[str, list[dict]] = {}
    for e in evaluations:
        checks = e.get("deterministic_checks") or {}
        for v in checks.get("violations", []):
            rule_id = v["rule_id"]
            rule_buckets.setdefault(rule_id, []).append({
                "profile_id": e["profile_id"],
                "severity": v["severity"],
                "week": v["week"],
                "description": v["description"],
            })

    for rule_id, occurrences in sorted(rule_buckets.items(), key=lambda x: -len(x[1])):
        severity = occurrences[0]["severity"]
        clusters.append({
            "type": "deterministic",
            "rule": rule_id,
            "severity": severity,
            "count": len(occurrences),
            "profile_ids": list({o["profile_id"] for o in occurrences}),
            "sample": occurrences[:3],  # 3 premiers exemples
        })

    # ── Cluster 2 : dimensions LLM basses ────────────────────────────────────
    dim_low: dict[str, list[str]] = {dim: [] for dim in _SCORE_DIMENSIONS}
    for e in evaluations:
        llm = e.get("llm_evaluation")
        if not llm or llm.get("error"):
            continue
        for dim in _SCORE_DIMENSIONS:
            score = llm.get("scores", {}).get(dim)
            if score is not None and score < LOW_DIMENSION_THRESHOLD:
                dim_low[dim].append(e["profile_id"])

    for dim, profile_ids in sorted(dim_low.items(), key=lambda x: -len(x[1])):
        if profile_ids:
            clusters.append({
                "type": "llm_low_dimension",
                "dimension": dim,
                "threshold": LOW_DIMENSION_THRESHOLD,
                "count": len(profile_ids),
                "profile_ids": profile_ids,
            })

    return clusters
