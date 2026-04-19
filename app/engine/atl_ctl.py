"""
Calcul ATL / CTL / TSB depuis l'historique des séances.

ATL (Acute Training Load)     = fatigue aiguë, EMA sur τ=7 jours
CTL (Chronic Training Load)   = fitness, EMA sur τ=42 jours
TSB (Training Stress Balance) = CTL - ATL = forme du jour

Formule EMA calendaire (série temporelle continue) :

    Pour chaque séance sur le jour d, précédée d'une séance sur le jour d_prev :

        gap = (d - d_prev).days          ← jours de repos entre les deux
        ATL[d] = ATL[d_prev] × exp(−gap/7)  + TSS[d] × (1 − exp(−1/7))
        CTL[d] = CTL[d_prev] × exp(−gap/42) + TSS[d] × (1 − exp(−1/42))

    Le terme exp(−gap/τ) applique le déclin naturel pour chaque jour sans activité.
    Sans cette correction, 5 jours de repos entre deux séances ne diminuent pas l'ATL.

Fonctions principales :
    compute_fitness(logs)                   → FitnessMetrics depuis SessionLog
    compute_fitness_from_any(items, ...)    → FitnessMetrics (duck-typing SessionLog/Activity)
    project_fitness_from_plan(plan, ...)    → list[FitnessProjectionPoint] (CTL théorique)
"""

from datetime import date, timedelta
import math
from dataclasses import dataclass


@dataclass
class FitnessMetrics:
    atl: float   # fatigue aiguë (7j)
    ctl: float   # fitness (42j)
    tsb: float   # forme = CTL - ATL


@dataclass
class FitnessProjectionPoint:
    date: date
    ctl: float        # CTL théorique si le plan est suivi parfaitement
    atl: float        # ATL théorique
    tsb: float        # TSB théorique
    week_number: int
    tss_planned: float  # TSS cible de la séance


_K_ATL = 1 - math.exp(-1 / 7)
_K_CTL = 1 - math.exp(-1 / 42)


def compute_fitness(logs: list) -> FitnessMetrics:
    """
    Calcule ATL/CTL/TSB depuis une liste de SessionLog.
    Seuls les logs status='done' avec tss_actual renseigné sont pris en compte.
    """
    atl = 0.0
    ctl = 0.0

    done_logs = sorted(
        [lg for lg in logs if lg.status == "done" and lg.tss_actual],
        key=lambda lg: lg.logged_date,
    )

    prev_date = None
    for log in done_logs:
        gap = (log.logged_date - prev_date).days if prev_date is not None else 1
        # 1. Déclin depuis la dernière séance (gap=0 → exp(0)=1, aucune perte)
        atl *= math.exp(-gap / 7)
        ctl *= math.exp(-gap / 42)
        # 2. Ajout du stress de la séance
        atl += log.tss_actual * _K_ATL
        ctl += log.tss_actual * _K_CTL
        # 3. Mise à jour de la date de référence
        prev_date = log.logged_date

    tsb = ctl - atl
    return FitnessMetrics(
        atl=round(atl, 1),
        ctl=round(ctl, 1),
        tsb=round(tsb, 1),
    )


def estimate_initial_ctl(weekly_tss: float) -> float:
    """
    CTL de départ estimé depuis le volume hebdomadaire.

    Le CTL d'équilibre (steady-state) d'un athlète s'entraînant à charge constante
    est égal au TSS moyen journalier = weekly_tss / 7.

    À utiliser pour amorcer l'EMA quand l'historique disponible est < 2×τ_CTL (84j),
    période en dessous de laquelle l'EMA n'a pas encore convergé depuis 0.
    """
    return round(weekly_tss / 7, 1)


def compute_fitness_from_any(
    items: list,
    initial_ctl: float = 0.0,
    seed_date=None,
    target_date=None
) -> FitnessMetrics:
    """
    Calcule ATL/CTL/TSB depuis une liste mixte de SessionLog ou Activity.
    Duck-typing : utilise .tss_actual/.logged_date (SessionLog) ou .tss/.activity_date (Activity).

    initial_ctl : CTL d'amorçage (défaut 0.0). Utiliser estimate_initial_ctl() si
    l'historique disponible est court (< 84j) pour éviter la sous-estimation EMA.

    seed_date : date à laquelle initial_ctl est appliqué (ex: date.today() - 49j pour
    l'historique Strava). Si fourni, le gap jusqu'à la première activité est calculé
    depuis seed_date (et non depuis 1 jour par convention), ce qui permet à l'EMA de
    décroître correctement entre le point d'amorçage et la première activité réelle.
    ATL n'est pas amorcé (τ=7j → convergence en ~3 semaines, suffisant avec 49j).
    """
    def _tss(item):
        return getattr(item, "tss_actual", None) or getattr(item, "tss", None)

    def _date(item):
        return getattr(item, "logged_date", None) or getattr(item, "activity_date", None)

    atl = 0.0
    ctl = initial_ctl
    if target_date is None:
        target_date = date.today()

    valid = sorted(
        [it for it in items if _tss(it) is not None],
        key=_date,
    )

    # Si un seed_date est fourni, la boucle commence depuis cette date
    # → le gap jusqu'à la première activité reflétera les jours de repos réels
    # entre T-49 (début import Strava) et la première séance trouvée.
    prev_date = seed_date  # None si pas de seed → gap=1 convention (comportement original)
    for item in valid:
        d = _date(item)
        tss = _tss(item)
        gap = (d - prev_date).days if prev_date is not None else 1
        # 1. Déclin depuis la dernière séance (gap=0 → exp(0)=1, aucune perte)
        atl *= math.exp(-gap / 7)
        ctl *= math.exp(-gap / 42)
        # 2. Ajout du stress de la séance
        atl += tss * _K_ATL
        ctl += tss * _K_CTL
        # 3. Mise à jour de la date de référence
        prev_date = d

    # --- ÉTAPE MANQUANTE : Le déclin final jusqu'à aujourd'hui ---
    if prev_date and prev_date < target_date:
        final_gap = (target_date - prev_date).days
        atl *= math.exp(-final_gap / 7)
        ctl *= math.exp(-final_gap / 42)

    tsb = ctl - atl
    return FitnessMetrics(
        atl=round(atl, 1),
        ctl=round(ctl, 1),
        tsb=round(tsb, 1),
    )


def project_fitness_from_plan(
    plan,
    initial_ctl: float,
    initial_atl: float = 0.0,
) -> list[FitnessProjectionPoint]:
    """
    Simule l'évolution CTL/ATL/TSB en suivant le plan parfaitement (TSS = tss_target).

    Même formule EMA que compute_fitness_from_any() — seule différence : les TSS
    utilisés sont les cibles du plan, pas des valeurs réelles.

    Retourne un point par séance planifiée, trié par date.
    Ce CTL théorique sert de référence pour évaluer la déviation du suivi réel.

    plan : TrainingPlanSchema (duck-typing — accède à .weeks[].start_date et
           .weeks[].sessions[].day_of_week / .tss_target)
    """
    # Collecter toutes les sessions avec leur date réelle
    # day_of_week 0=Lundi, start_date est toujours un lundi (plan_builder garantit ça)
    items: list[tuple[date, float, int]] = []
    for week in plan.weeks:
        if week.start_date is None:
            continue
        for session in week.sessions:
            session_date = week.start_date + timedelta(days=session.day_of_week)
            items.append((session_date, session.tss_target, week.week_number))

    items.sort(key=lambda x: x[0])

    atl = initial_atl
    ctl = initial_ctl
    prev_date: date | None = None
    result: list[FitnessProjectionPoint] = []

    for session_date, tss, week_number in items:
        gap = (session_date - prev_date).days if prev_date is not None else 1
        atl *= math.exp(-gap / 7)
        ctl *= math.exp(-gap / 42)
        atl += tss * _K_ATL
        ctl += tss * _K_CTL
        prev_date = session_date

        result.append(FitnessProjectionPoint(
            date=session_date,
            ctl=round(ctl, 1),
            atl=round(atl, 1),
            tsb=round(ctl - atl, 1),
            week_number=week_number,
            tss_planned=round(tss, 1),
        ))

    return result


def tsb_label(tsb: float) -> str:
    """
    Interprétation courte du TSB pour affichage rapide.

    Bandes calibrées pour la compétition cycliste :
      < -30          → surmenage (risque de surentraînement)
      -30 à  0       → fatigue normale (charge en cours)
        0 à +5       → bonne forme (juste équilibré)
       +5 à +15      → forme de pointe — zone cible affûtage (peak performance)
      +15 à +20      → très frais (bénéfique en semaine de taper)
       > +20         → transition — risque de désentraînement si prolongé
    """
    if tsb < -30:
        return "🔴 Surmenage"
    if tsb < 0:
        return "🟡 Fatigue normale"
    if tsb <= 5:
        return "🟢 Bonne forme"
    if tsb <= 15:
        return "✨ Forme de pointe"
    if tsb <= 20:
        return "🔵 Très frais"
    return "⚪ Transition — risque de désentraînement"


def tss_from_rpe(duration_minutes: int, rpe_emoji: str) -> float:
    """Calcule un TSS approximatif depuis la durée et le ressenti."""
    rpe_values = {"hard": 8, "normal": 5, "easy": 3}
    rpe = rpe_values.get(rpe_emoji, 5)
    return round((duration_minutes / 60) * rpe * rpe * 10, 1)
