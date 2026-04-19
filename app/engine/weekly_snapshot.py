"""
Snapshot hebdomadaire de la charge d'entraînement.

Calculs déterministes — le LLM interprète, n'effectue aucun calcul.

Indicateurs fournis :
  - tss_7d             : charge totale des 7 derniers jours
  - tss_6w_avg         : charge hebdo moyenne des 6 semaines précédentes
  - load_trend_pct     : tendance de charge vs moyenne 6 semaines (%)
  - sessions_done_7d   : nombre de séances réalisées sur 7 jours
  - monotony_index     : formule Foster = mean(TSS journaliers) / std(TSS journaliers)
                         Un indice élevé (>2.0) = charge monotone = risque de surmenage silencieux.
                         None si <2 jours d'activité (std non calculable).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, stdev


@dataclass
class WeeklySnapshot:
    tss_7d: float                 # TSS total des 7 derniers jours (inclus aujourd'hui)
    tss_6w_avg: float             # TSS hebdo moyen des 6 semaines précédentes
    load_trend_pct: float         # (tss_7d - tss_6w_avg) / tss_6w_avg * 100 ; 0.0 si pas de base
    sessions_done_7d: int         # séances "done" dans les 7 derniers jours
    monotony_index: float | None  # Foster : mean/std — None si <2 jours actifs


def _item_date(it) -> date | None:
    """Duck-typing : SessionLog utilise logged_date, Activity utilise activity_date."""
    return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)


def _item_tss(it) -> float | None:
    """Duck-typing : SessionLog utilise tss_actual, Activity utilise tss."""
    return getattr(it, "tss_actual", None) or getattr(it, "tss", None)


def _item_is_done(it) -> bool:
    """SessionLog doit avoir status='done'. Activity est toujours comptée si TSS présent."""
    status = getattr(it, "status", None)
    return status is None or status == "done"


def compute_weekly_snapshot(logs: list, today: date) -> WeeklySnapshot:
    """
    Calcule le snapshot hebdomadaire depuis la liste de logs déjà en mémoire.
    Accepte des SessionLog et/ou des Activity Strava (duck-typing).

    Args:
        logs  : liste mixte SessionLog / Activity, triée ou non
        today : date de référence (date.today() en production)

    Returns:
        WeeklySnapshot avec toutes les métriques pré-calculées.
    """
    cutoff_7d = today - timedelta(days=6)  # fenêtre inclusive [today-6 .. today]

    # ── Fenêtre 7 jours ──────────────────────────────────────────────────────
    recent = [
        it for it in logs
        if _item_is_done(it)
        and _item_tss(it)
        and cutoff_7d <= (_item_date(it) or date.min) <= today
    ]
    tss_7d = sum(_item_tss(it) for it in recent)
    # sessions_done_7d : uniquement les SessionLog (status explicite) — pas les Activity Strava
    sessions_done_7d = sum(1 for it in recent if getattr(it, "status", None) == "done")

    # TSS par jour (plusieurs séances/jour cumulées)
    daily_map: dict[date, float] = {}
    for it in recent:
        d = _item_date(it)
        daily_map[d] = daily_map.get(d, 0.0) + _item_tss(it)

    daily_values = list(daily_map.values())

    # Monotonie Foster : mean / std (score élevé = charge uniforme = danger)
    if len(daily_values) >= 2:
        d_mean = mean(daily_values)
        d_std = stdev(daily_values)
        monotony_index = round(d_mean / d_std, 2) if d_std > 0 else None
    else:
        monotony_index = None

    # ── Fenêtre 6 semaines précédentes (sans chevauchement avec les 7 derniers jours) ─
    weekly_tss: list[float] = []
    for week_offset in range(1, 7):
        week_end = today - timedelta(days=7 * week_offset)
        week_start = week_end - timedelta(days=6)
        week_sum = sum(
            _item_tss(it) for it in logs
            if _item_is_done(it)
            and _item_tss(it)
            and week_start <= (_item_date(it) or date.min) <= week_end
        )
        weekly_tss.append(week_sum)

    tss_6w_avg = round(mean(weekly_tss), 1) if weekly_tss else 0.0
    load_trend_pct = (
        round((tss_7d - tss_6w_avg) / tss_6w_avg * 100, 1)
        if tss_6w_avg > 0
        else 0.0
    )

    return WeeklySnapshot(
        tss_7d=round(tss_7d, 1),
        tss_6w_avg=tss_6w_avg,
        load_trend_pct=load_trend_pct,
        sessions_done_7d=sessions_done_7d,
        monotony_index=monotony_index,
    )
