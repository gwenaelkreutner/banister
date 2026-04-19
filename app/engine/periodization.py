"""
Découpage en phases d'entraînement et calcul des TSS hebdomadaires cibles.

Phases : base → build → peak → taper
Règle de progression : ramp rate CTL dynamique selon le niveau (fallback +10%/sem)
Semaine de récupération toutes les 4 semaines (TSS × 0.6)
"""

import math
from dataclasses import dataclass
from typing import Literal

# Déclin CTL sur 7 jours (τ=42j) — utilisé pour convertir ramp rate CTL → max TSS hebdo
# Formule : K_WEEKLY = 1 - exp(-7/42) = 1 - exp(-1/6) ≈ 0.1535
_K_WEEKLY = 1.0 - math.exp(-1.0 / 6.0)

# Profil de charge intra-bloc 3+1 (nominal → charge → surcharge avant semaine de récup)
# Appliqué en phases build et peak pour créer des pics de fatigue réels (TSB < -15 à -20)
# plutôt qu'une montée linéaire trop lisse qui ne stimule pas la surcompensation.
#
# IMPORTANT — les trois semaines de charge sont TOUTES au-dessus de la cible linéaire :
#   ×1.00 = cible nominale (entretien CTL à ce niveau)
#   ×1.10 = surcharge modérée
#   ×1.20 = surcharge maximale, plafonnée à peak_tss
#
# Un pattern (0.85, 1.00, 1.15) était incorrect : la semaine ×0.85 tombe en dessous
# du TSS d'entretien du CTL courant (ATL descend, CTL aussi) → régression pendant
# les semaines censées être "en charge". Le CTL ne peut croître que si le TSS moyen
# hebdomadaire dépasse le CTL courant × 7.
_BLOCK_WAVE = (1.00, 1.10, 1.20)

# Facteur de réduction TSS pour les semaines de récupération.
# 0.70 = 70 % de la cible de base.
# Ancien: 0.65 → poussait le TSS sous le niveau de maintien CTL pour les athlètes
# avancés (CTL=54 → besoin 378 TSS/sem ; 65% de 387 = 251 TSS → déclin CTL garanti).
# 0.70 garantit une décharge visible (30% de réduction) tout en limitant le déclin CTL.
_RECOVERY_FACTOR = 0.70

# Fréquence des semaines de récupération.
# Pattern 3+1 standard (3 semaines de charge + 1 récupération) quelle que soit la durée
# du plan. Ce pattern assure au plus 3 semaines consécutives sans récupération, ce qui
# est la limite recommandée avant l'accumulation de fatigue chronique.
def _recovery_every_n(weeks_total: int) -> int:
    return 3

Phase = Literal["base", "build", "peak", "taper"]


@dataclass
class PhaseConfig:
    phase: Phase
    weeks: int
    tss_multiplier_start: float
    tss_multiplier_end: float


def _max_ramp_rate_from_ctl(ctl: float, level: str = "intermediate") -> float:
    """
    Ramp rate CTL max par semaine.

    Deux axes pris en compte :
    1. CTL biométrique (capacité physiologique actuelle)
    2. Niveau déclaré (tolérance à la fatigue, historique d'entraînement)

    Pour un athlète "advanced" ou "expert", le ramp rate est déplafonné à 8 CTL/sem
    indépendamment du CTL actuel — le seuil CTL < 70 était calibré pour des profils
    intermédiaires qui n'ont pas l'habituation à l'accumulation de fatigue.

    Calibré d'après les recommandations Coggan / TrainingPeaks :
      CTL < 35  → débutant     → 4 CTL/sem
      CTL 35-70 → intermédiaire → 6 CTL/sem
      CTL > 70  → avancé       → 8 CTL/sem

    Conversion en max TSS hebdomadaire :
      ΔCTL/sem = K_WEEKLY × (TSS_daily − CTL)
      → max_TSS_weekly = 7 × (CTL + ramp_rate / K_WEEKLY)
    """
    if level in ("advanced", "expert"):
        return 8.0   # tolérance fatigue élevée, indépendant du CTL courant
    if ctl < 35:
        return 4.0
    if ctl < 70:
        return 6.0
    return 8.0


def _base_reduction_from_ctl(ctl: float) -> float:
    """
    Facteur de réduction de la phase base selon le CTL actuel (0.0 → 0.5).

    Le CTL (en TSS/jour) reflète la base aérobie déjà construite.
    Un CTL élevé signifie que l'athlète n'a pas besoin de tout reconstruire depuis zéro.

    Seuils calibrés pour le cyclisme :
      CTL < 30  → base insuffisante, plan normal (pas de réduction)
      CTL 30-50 → base correcte → -20 % de la phase base
      CTL 50-70 → bonne base (ex: hiver actif) → -35 %
      CTL > 70  → très bonne base → -50 %
    """
    if ctl < 30:
        return 0.0
    if ctl < 50:
        return 0.2
    if ctl < 70:
        return 0.35
    return 0.5


def compute_phase_sequence(
    weeks_total: int,
    current_ctl: float | None = None,
) -> list[PhaseConfig]:
    """
    Retourne la séquence de phases selon le nombre de semaines disponibles.
    Taper et peak sont garantis à au moins 1 semaine.

    current_ctl : CTL journalier actuel (optionnel). Si fourni et élevé, la phase
    base est raccourcie et les semaines économisées sont ajoutées au build.
    """
    if weeks_total < 4:
        # Très court : juste un bloc build + taper
        taper_w = 1
        build_w = max(1, weeks_total - taper_w)
        return [
            PhaseConfig("build", build_w, 0.80, 1.0),
            PhaseConfig("taper", taper_w, 0.55, 0.55),
        ]

    if weeks_total < 8:
        taper_w = max(1, int(weeks_total * 0.20))
        peak_w = max(1, int(weeks_total * 0.20))
        build_w = weeks_total - taper_w - peak_w
        return [
            PhaseConfig("build", build_w, 0.75, 1.0),
            PhaseConfig("peak",  peak_w,  1.0,  1.0),
            PhaseConfig("taper", taper_w, 0.55, 0.55),
        ]

    if weeks_total <= 16:
        taper_w = max(1, int(weeks_total * 0.10))
        peak_w  = max(1, int(weeks_total * 0.15))
        build_w = max(2, int(weeks_total * 0.35))
        base_w  = weeks_total - taper_w - peak_w - build_w
        if current_ctl:
            reduction = _base_reduction_from_ctl(current_ctl)
            weeks_saved = min(round(base_w * reduction), base_w - 1)
            base_w  -= weeks_saved
            build_w += weeks_saved
        return [
            PhaseConfig("base",  base_w,  0.65, 0.80),
            PhaseConfig("build", build_w, 0.80, 1.0),
            PhaseConfig("peak",  peak_w,  0.95, 0.95),  # proche du build — taper = seule vraie réduction
            PhaseConfig("taper", taper_w, 0.55, 0.55),
        ]

    # > 16 semaines
    taper_w = max(1, int(weeks_total * 0.08))
    peak_w  = max(1, int(weeks_total * 0.12))
    build_w = max(3, int(weeks_total * 0.30))
    base_w  = weeks_total - taper_w - peak_w - build_w
    if current_ctl:
        reduction = _base_reduction_from_ctl(current_ctl)
        weeks_saved = min(round(base_w * reduction), base_w - 1)
        base_w  -= weeks_saved
        build_w += weeks_saved
    return [
        PhaseConfig("base",  base_w,  0.55, 0.75),
        PhaseConfig("build", build_w, 0.75, 1.0),
        PhaseConfig("peak",  peak_w,  0.95, 0.95),  # proche du build — taper = seule vraie réduction
        PhaseConfig("taper", taper_w, 0.55, 0.55),
    ]


def compute_weekly_tss_targets(
    initial_tss: float,
    peak_tss: float,
    phases: list[PhaseConfig],
    initial_ctl: float | None = None,
    initial_tsb: float | None = None,
    level: str = "intermediate",
) -> list[tuple[Phase, float, bool]]:
    """
    Retourne une liste de (phase, tss_cible, is_recovery_week) pour chaque semaine.

    Semaines de récupération :
      - Compteur GLOBAL (toutes phases confondues) — le compteur per-phase se réinitialisait
        à chaque phase et n'atteignait jamais 4 sur les plans courts (≤10 semaines).
      - Fréquence : toutes les 3 sem pour plans ≤10 sem, toutes les 4 sem pour plans >10 sem.
      - La phase taper est exclue (elle réduit déjà le TSS à 55 %).
      - TSS recovery = base_target × _RECOVERY_FACTOR (0.65).

    Wave loading (build/peak) :
      - La base TSS est fixée AU DÉBUT de chaque bloc de charge (non interpolée sur toute la phase).
      - Cela évite le double-comptage linéaire × wave qui créait des sauts >20 % entre semaines.
      - La progression entre blocs est assurée par le démarrage de chaque nouveau bloc sur la
        cible linéaire courante.
      - charge_week est remis à 0 après chaque semaine de récupération.

    Contrainte de progression :
      - Si initial_ctl fourni : ramp rate CTL dynamique (4/6/8 CTL/sem selon niveau)
        converti en max TSS hebdo via la formule EMA τ=42j.
        Le CTL estimé est mis à jour semaine par semaine pour rester cohérent.
      - Sinon : fallback +10%/semaine (comportement historique).
    """
    weeks: list[tuple[Phase, float, bool]] = []
    current_tss = initial_tss

    if initial_ctl is not None:
        max_ramp_rate = _max_ramp_rate_from_ctl(initial_ctl, level=level)
        ctl_estimate = initial_ctl
    else:
        max_ramp_rate = None
        ctl_estimate = initial_tss / 7.0

    weeks_total = sum(p.weeks for p in phases)
    n_recovery = _recovery_every_n(weeks_total)
    global_week = 0  # compteur global utilisé pour placer les semaines de récup

    # charge_week et block_base sont GLOBAUX (partagés entre phases).
    # L'ancienne initialisation à l'intérieur de la boucle de phase les remettait à 0
    # à chaque changement de phase, ce qui empêchait le ×1.20 de se déclencher sur les
    # plans courts (≤10 sem) : seules 2 semaines consécutives étaient disponibles avant
    # qu'une récup ou un changement de phase ne remette le compteur à 0.
    # Avec un suivi global, la PEAK S1 hérite du charge_week=2 accumulé en BUILD → ×1.20.
    charge_week = 0   # position dans le bloc de charge : 0=nominal, 1=charge, 2=surcharge
    block_base: float | None = None  # base TSS fixée au début de chaque bloc wave

    # Cap wave avancé : pour advanced/expert, le wave peut dépasser peak_tss de 30 %
    # pour produire les TSB -20 à -25 attendus en fin de bloc de charge.
    # Pour les autres niveaux, le cap reste peak_tss (comportement classique).
    wave_cap = peak_tss * 1.30 if level in ("advanced", "expert") else peak_tss

    for phase in phases:
        tss_start = peak_tss * phase.tss_multiplier_start
        tss_end = peak_tss * phase.tss_multiplier_end

        # Plancher pour base et build : ne jamais démarrer sous le volume actuel de l'athlète.
        # Sans ce plancher, un athlète CTL=52 voit la base démarrer à 0.55×peak=300 TSS — soit
        # 18% sous son niveau courant — provoquant une baisse de CTL pendant la phase de base.
        if phase.phase in ("base", "build"):
            tss_start = max(tss_start, initial_tss)
            tss_end = max(tss_end, initial_tss)

        for i in range(phase.weeks):
            global_week += 1
            # Semaine de récup : compteur global, sauf pour le taper (déjà réduit à 55 %)
            # La phase "peak" est exclue des semaines de récup : elle représente le point
            # culminant du plan (charge maximale avant taper) et ne doit jamais être dégradée
            # en décharge. Avec un plan de 10 sem (pattern 3+1), la sem 9 tombait en PEAK
            # et devenait recovery, effaçant tout le bénéfice du bloc build précédent.
            is_recovery = (global_week % n_recovery == 0 and phase.phase not in ("taper", "peak"))

            # Interpolation linéaire dans la phase : donne la charge "centrale" du bloc
            progress = i / max(phase.weeks - 1, 1) if phase.weeks > 1 else 0.0
            base_target = tss_start + progress * (tss_end - tss_start)

            if is_recovery:
                # Cap additionnel : la récupération ne doit jamais dépasser 82% de la semaine
                # précédente. Le seuil était à 0.73, ce qui poussait le TSS sous le niveau de
                # maintien CTL (CTL × 7) pour les athlètes entraînés, générant un déclin
                # systématique de la condition physique à chaque semaine de récup.
                # 0.82 garantit une réduction visible (-18%) tout en limitant le déclin CTL.
                target = min(base_target * _RECOVERY_FACTOR, current_tss * 0.82)
                charge_week = 0      # le prochain bloc repart à wave ×1.00
                block_base = None    # sera recalculé au prochain bloc
            else:
                # Wave loading en build/peak uniquement (disponible si CTL connu pour le contrôle
                # physiologique via ramp rate — le fallback +10% ne supporte pas les pics wave).
                # Pattern 3+1 : nominal(×1.00) → charge(×1.10) → surcharge(×1.20) → récup
                #
                # IMPORTANT — on fixe block_base AU DÉBUT du bloc (charge_week == 0) plutôt que
                # d'utiliser base_target (qui croît linéairement). Cela évite le double-comptage
                # interpolation linéaire × multiplicateur wave qui générait des sauts >20 %.
                # La progression entre blocs est naturelle : chaque nouveau bloc démarre sur la
                # cible linéaire courante (plus haute que le bloc précédent).
                if phase.phase in ("build", "peak") and max_ramp_rate is not None:
                    if charge_week % 3 == 0:
                        block_base = base_target  # fixer la base pour ce bloc
                    wave_mul = _BLOCK_WAVE[charge_week % 3]
                    target = min((block_base or base_target) * wave_mul, wave_cap)
                else:
                    target = base_target

                # Cap hebdomadaire : garde-fou contre les sauts trop brutaux du wave loading.
                # Pour les profils advanced/expert, on autorise 15% max d'augmentation par
                # semaine (vs 10% pour les autres). Justification : leur tolérance à la fatigue
                # est plus élevée et le ramp rate CTL déjà réglé à 8 assure la protection
                # physiologique principale. Le 10% était trop conservateur et empêchait
                # d'atteindre TSB -20 à -25 (objectif pour un bloc de charge avancé).
                _weekly_cap = 1.149 if level in ("advanced", "expert") else 1.099

                if max_ramp_rate is not None:
                    # Contrainte ramp rate CTL : max TSS qui respecte ΔCTL ≤ ramp_rate/sem
                    # Dérivé de : ΔCTL/sem = K_WEEKLY × (TSS_daily − CTL)
                    max_tss_from_ramp = 7.0 * (ctl_estimate + max_ramp_rate / _K_WEEKLY)
                    target = min(target, max_tss_from_ramp)
                    target = min(target, current_tss * _weekly_cap)
                else:
                    # Fallback sans CTL : contrainte hebdo selon le niveau.
                    target = min(target, current_tss * _weekly_cap)

                charge_week += 1

                # Taper : garantir une réduction significative par rapport à la semaine précédente.
                # Sans ce cap, le ramp rate CTL peut contraindre S(n-1) au même niveau que le taper,
                # déclenchant `taper_not_lighter`. On fixe max 80% du volume courant.
                if phase.phase == "taper" and current_tss > 0:
                    target = min(target, current_tss * 0.80)

            target = round(target, 1)
            weeks.append((phase.phase, target, is_recovery))

            # Mettre à jour les estimateurs pour la semaine suivante
            if not is_recovery:
                current_tss = target
            # CTL suit l'EMA quelle que soit la semaine (récup comprise)
            ctl_estimate = ctl_estimate * (1.0 - _K_WEEKLY) + (target / 7.0) * _K_WEEKLY

    return weeks


def compute_peak_tss(initial_tss: float, level: str, structured_history: bool) -> float:
    """
    TSS peak cible selon niveau et expérience.
    initial_tss : TSS hebdo actuel de l'athlète.
    """
    multipliers = {
        ("beginner",     False): (1.6, 350),
        ("beginner",     True):  (1.8, 450),
        ("intermediate", False): (1.5, 600),
        ("intermediate", True):  (1.5, 650),
        # 1.60 → peak 372×1.60=595 TSS/sem (60 TSS/h sur 10h — atteignable Z2/Z3/Z4).
        # Combiné au wave ×1.20 (PEAK) et à l'endurance fill Z3, la charge réelle
        # livrée vise 550-600 TSS/sem en semaine de pic → TSB -20 à -25, CTL→65-70.
        ("advanced",     False): (1.60, 900),
        ("advanced",     True):  (1.60, 950),
        ("expert",       False): (1.2, 950),
        ("expert",       True):  (1.2, 1050),
    }
    mult, cap = multipliers.get((level, structured_history), (1.5, 600))
    return min(initial_tss * mult, cap)
