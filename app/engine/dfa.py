"""DFA α1 — analyse par franchissement de bande, porté depuis un script tiers
(Section11, fourni par l'utilisateur), seuils/algorithme vérifiés contre le script
source le 2026-09-21.

**Consomme, ne recalcule pas** (Principe IV) : AlphaHRV (champ Garmin Connect IQ) calcule
déjà l'exposant DFA α1 sur la montre en continu pendant la sortie ; le stream `dfa_a1`
remonte pré-calculé via intervals.icu (`client.get_activity_streams()`, vérifié contre
l'API réelle 2026-09-21 — voir `app/providers/intervals/streams.py`). Ce module ne fait
que filtrer le bruit et détecter les franchissements de seuil sur la série déjà calculée
— aucune ré-analyse DFA depuis des intervalles RR bruts.

⚠️ Aucune activité AlphaHRV réelle n'existe encore sur le compte de test au moment
d'écrire ce module (vérifié 2026-09-21 : `dfa_a1` absent des streams disponibles sur la
dernière sortie). L'algorithme est porté fidèlement et testé sur des séries synthétiques
(`tests/test_engine/test_dfa.py`), mais n'a pas encore été validé contre une vraie
lecture AlphaHRV — à revisiter dès la première activité réelle enregistrée.

Seuils LT1/LT2 : validés cyclisme (Rowlands et al. 2017, Gronwald 2020, Mateo-March et
al. 2023, cités dans le script source). Repris tels quels pour le cyclisme ; les autres
sports recevraient un rollup mais devraient être marqués `validated=False` si ce module
est un jour étendu au-delà du vélo (non fait ici — Banister est cyclisme-only en
pratique).
"""
from __future__ import annotations

from dataclasses import dataclass

DFA_LT1 = 1.0    # DFA α1 au-dessus de ce seuil = en dessous de LT1 (aérobie franc)
DFA_LT2 = 0.5    # DFA α1 en dessous de ce seuil = au-dessus de LT2 (supra-seuil)
DFA_LT1_BAND = 0.05   # fenêtre de franchissement LT1 : 0.95-1.05
DFA_LT2_BAND = 0.05   # fenêtre de franchissement LT2 : 0.45-0.55
DFA_MIN_CROSSING_DWELL_SECS = 60      # secondes min dans la bande pour émettre une estimation
# Rejette les secondes où artifacts% dépasse ce seuil (convention Altini).
DFA_ARTIFACT_MAX_PCT = 5.0
DFA_MIN_VALID_VALUE = 0.01            # exclut les zéros-sentinelles AlphaHRV
DFA_MIN_DURATION_SECS = 1200          # 20min de données valides minimum pour sufficient=True
DFA_SUFFICIENT_MIN_VALID_PCT = 70.0   # valid_pct minimum pour sufficient=True
# >15% du temps au-dessus de LT2 = dérive non interprétable (intervalles, pas steady-state).
DFA_DRIFT_INTERPRETABLE_MAX_LT2_PCT = 15.0


@dataclass(frozen=True)
class BandStats:
    secs: int
    pct: float
    avg_hr: float | None
    avg_watts: float | None


@dataclass(frozen=True)
class CrossingStats:
    secs_in_band: int
    avg_hr: float | None
    avg_watts: float | None


@dataclass(frozen=True)
class DriftInfo:
    first_third_avg: float
    last_third_avg: float
    delta: float
    interpretable: bool


@dataclass(frozen=True)
class DFAQuality:
    valid_secs: int
    total_secs: int
    valid_pct: float
    artifact_rate_avg: float | None
    sufficient: bool


@dataclass(frozen=True)
class DFABlock:
    avg: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    tiz_below_lt1: BandStats | None
    tiz_lt1_transition: BandStats | None
    tiz_transition_lt2: BandStats | None
    tiz_above_lt2: BandStats | None
    drift: DriftInfo | None
    lt1_crossing: CrossingStats | None
    lt2_crossing: CrossingStats | None
    quality: DFAQuality


def _insufficient_block(quality: DFAQuality) -> DFABlock:
    return DFABlock(
        avg=None, p25=None, p50=None, p75=None,
        tiz_below_lt1=None, tiz_lt1_transition=None,
        tiz_transition_lt2=None, tiz_above_lt2=None,
        drift=None, lt1_crossing=None, lt2_crossing=None,
        quality=quality,
    )


def compute_dfa_block(streams: dict[str, list]) -> DFABlock | None:
    """`streams` : dict `{type: data}` déjà normalisé (voir
    `app/providers/intervals/streams.py::streams_to_dict`), clés attendues `dfa_a1`
    (obligatoire), `artifacts`/`heartrate`/`watts` (optionnels, dégradent juste la
    sortie s'ils manquent).

    `None` si le stream `dfa_a1` est absent — pas d'enregistrement AlphaHRV sur cette
    activité (distinct d'un bloc présent avec `quality.sufficient=False`, qui signifie
    qu'AlphaHRV a tourné mais que les données sont inutilisables).

    Filtres appliqués, dans l'ordre :
      1. Rejette les secondes où `dfa_a1 < DFA_MIN_VALID_VALUE` (zéros-sentinelles)
      2. Rejette les secondes où `artifacts > DFA_ARTIFACT_MAX_PCT`
    Les deux filtres s'appliquent conjointement à dfa_a1/hr/watts pour rester alignés.
    """
    dfa_stream = streams.get("dfa_a1")
    if not dfa_stream:
        return None

    n = len(dfa_stream)
    artifacts_stream = streams.get("artifacts") or [0.0] * n
    hr_stream = streams.get("heartrate") or [None] * n
    watts_stream = streams.get("watts") or [None] * n
    if len(artifacts_stream) != n:
        artifacts_stream = (list(artifacts_stream) + [0.0] * n)[:n]
    if len(hr_stream) != n:
        hr_stream = (list(hr_stream) + [None] * n)[:n]
    if len(watts_stream) != n:
        watts_stream = (list(watts_stream) + [None] * n)[:n]

    valid_dfa: list[float] = []
    valid_hr: list[float | None] = []
    valid_watts: list[float | None] = []
    artifact_sum = 0.0
    artifact_count = 0
    for i in range(n):
        d = dfa_stream[i]
        a = artifacts_stream[i]
        if a is not None:
            artifact_sum += a
            artifact_count += 1
        if d is None or d < DFA_MIN_VALID_VALUE:
            continue
        if a is not None and a > DFA_ARTIFACT_MAX_PCT:
            continue
        valid_dfa.append(d)
        valid_hr.append(hr_stream[i])
        valid_watts.append(watts_stream[i])

    valid_secs = len(valid_dfa)
    total_secs = n
    valid_pct = round(100.0 * valid_secs / total_secs, 1) if total_secs else 0.0
    artifact_rate_avg = round(artifact_sum / artifact_count, 2) if artifact_count else None
    sufficient = (
        valid_secs >= DFA_MIN_DURATION_SECS and valid_pct >= DFA_SUFFICIENT_MIN_VALID_PCT
    )
    quality = DFAQuality(
        valid_secs=valid_secs, total_secs=total_secs, valid_pct=valid_pct,
        artifact_rate_avg=artifact_rate_avg, sufficient=sufficient,
    )

    if not sufficient:
        return _insufficient_block(quality)

    sorted_dfa = sorted(valid_dfa)
    avg = round(sum(valid_dfa) / valid_secs, 3)
    p25 = round(sorted_dfa[valid_secs // 4], 3)
    p50 = round(sorted_dfa[valid_secs // 2], 3)
    p75 = round(sorted_dfa[(valid_secs * 3) // 4], 3)

    def _band_stats(predicate) -> BandStats | None:
        secs = 0
        hr_sum = hr_n = 0
        w_sum = w_n = 0
        for i in range(valid_secs):
            if not predicate(valid_dfa[i]):
                continue
            secs += 1
            if valid_hr[i] is not None:
                hr_sum += valid_hr[i]
                hr_n += 1
            if valid_watts[i] is not None:
                w_sum += valid_watts[i]
                w_n += 1
        if secs == 0:
            return None
        return BandStats(
            secs=secs, pct=round(100.0 * secs / valid_secs, 1),
            avg_hr=round(hr_sum / hr_n) if hr_n else None,
            avg_watts=round(w_sum / w_n) if w_n else None,
        )

    tiz_below_lt1 = _band_stats(lambda d: d > DFA_LT1)
    tiz_lt1_transition = _band_stats(lambda d: 0.75 <= d <= DFA_LT1)
    tiz_transition_lt2 = _band_stats(lambda d: DFA_LT2 <= d < 0.75)
    tiz_above_lt2 = _band_stats(lambda d: d < DFA_LT2)

    third = valid_secs // 3
    drift: DriftInfo | None = None
    if third >= 60:  # au moins 60s par tiers pour une dérive significative
        first_third = valid_dfa[:third]
        last_third = valid_dfa[-third:]
        first_avg = round(sum(first_third) / len(first_third), 3)
        last_avg = round(sum(last_third) / len(last_third), 3)
        above_lt2_pct = tiz_above_lt2.pct if tiz_above_lt2 else 0.0
        drift = DriftInfo(
            first_third_avg=first_avg, last_third_avg=last_avg,
            delta=round(last_avg - first_avg, 3),
            interpretable=above_lt2_pct <= DFA_DRIFT_INTERPRETABLE_MAX_LT2_PCT,
        )

    def _crossing_stats(center: float, band: float) -> CrossingStats:
        lo, hi = center - band, center + band
        secs = 0
        hr_sum = hr_n = 0
        w_sum = w_n = 0
        for i in range(valid_secs):
            if not (lo <= valid_dfa[i] <= hi):
                continue
            secs += 1
            if valid_hr[i] is not None:
                hr_sum += valid_hr[i]
                hr_n += 1
            if valid_watts[i] is not None:
                w_sum += valid_watts[i]
                w_n += 1
        if secs < DFA_MIN_CROSSING_DWELL_SECS:
            return CrossingStats(secs_in_band=secs, avg_hr=None, avg_watts=None)
        return CrossingStats(
            secs_in_band=secs,
            avg_hr=round(hr_sum / hr_n) if hr_n else None,
            avg_watts=round(w_sum / w_n) if w_n else None,
        )

    lt1_crossing = _crossing_stats(DFA_LT1, DFA_LT1_BAND)
    lt2_crossing = _crossing_stats(DFA_LT2, DFA_LT2_BAND)

    return DFABlock(
        avg=avg, p25=p25, p50=p50, p75=p75,
        tiz_below_lt1=tiz_below_lt1, tiz_lt1_transition=tiz_lt1_transition,
        tiz_transition_lt2=tiz_transition_lt2, tiz_above_lt2=tiz_above_lt2,
        drift=drift, lt1_crossing=lt1_crossing, lt2_crossing=lt2_crossing,
        quality=quality,
    )
