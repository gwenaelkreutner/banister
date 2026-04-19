# Banister — Logique Métier & Règles de Calcul

## Principe fondateur

**Le LLM ne calcule jamais la charge d'entraînement.**

Toute décision quantitative (TSS, zones, durées, ATL/CTL/TSB) est produite par le moteur déterministe Python. Le LLM génère uniquement du texte (narration, interprétation, coaching) et peut appeler des outils qui délèguent au moteur.

---

## 1. Zones d'entraînement (`engine/zones.py`)

### Zones puissance (% FTP)
| Zone | Nom | % FTP bas | % FTP haut |
|------|-----|-----------|------------|
| Z1 | Récupération active | 0% | 55% |
| Z2 | Endurance | 55% | 75% |
| Z3 | Tempo | 75% | 87% |
| Z4 | Seuil lactique | 87% | 95% |
| Z5 | VO2 Max | 95% | 106% |
| Z6 | Anaérobie | 106% | 150% |

### Zones fréquence cardiaque (méthode Karvonen — % réserve cardiaque)
```
FC cible = FC repos + %FCR × (FC max − FC repos)
```

| Zone | Nom | % FCR bas | % FCR haut |
|------|-----|-----------|------------|
| Z1 | Récupération active | 0% | 50% |
| Z2 | Endurance | 50% | 65% |
| Z3 | Tempo | 65% | 78% |
| Z4 | Seuil lactique | 78% | 88% |
| Z5 | VO2 Max | 88% | 95% |
| Z6 | Anaérobie | 95% | 100% |

**Choix du mode** : si FTP disponible → zones power. Sinon → zones FC.
`coaching_mode` = `"power"` ou `"hr"` — déterminé à l'onboarding.

---

## 2. Calcul TSS (`engine/tss.py`)

### Priorité de calcul dans `SessionAnalyzer.analyze()`

```
1. Power (NP + FTP disponibles)   → TSS power — formule classique Coggan
2. HRSS (streams HR + physio)     → TRIMP de Banister normalisé — haute fidélité
3. Fallback scalaire              → avg_hr ou suffer_score (import historique)
```

---

### 2.1 Mode puissance (capteur de puissance)

```
IF  = NP / FTP                             (Intensity Factor)
TSS = (durée_s × NP × IF) / (FTP × 3600) × 100
    = durée_h × IF² × 100
```

NP (Normalized Power) — ordre de priorité :
1. `weighted_average_watts` Strava (si disponible)
2. NP calculé depuis les watts-streams : moyenne mobile 30s → racine 4e puissance de la moyenne des puissances 4e

Facteurs IF approx. par zone (utilisés pour l'estimation si NP non disponible) :
| Zone | IF |
|------|-----|
| Z1 | 0.48 |
| Z2 | 0.65 |
| Z3 | 0.81 |
| Z4 | 0.91 |
| Z5 | 1.00 |
| Z6 | 1.20 |

---

### 2.2 Mode HR haute fidélité — HRSS (TRIMP de Banister)

Utilisé quand la puissance n'est pas disponible mais que les **streams HR secondaires** (série temporelle par seconde) sont présents. Remplace l'ancienne heuristique `durée_h × TSS/heure_zone`.

#### Formule TRIMP de Banister

```
Pour chaque intervalle i entre deux échantillons consécutifs :

  HRR_i  = (HR_i − HR_repos) / (HR_max − HR_repos)       ∈ [0, 1]
  stress_i = dt_i × HRR_i × 0.64 × exp(k × HRR_i)

  k = 1.92  (hommes)
  k = 1.67  (femmes)

TRIMP_total = Σ stress_i
```

**Pourquoi exponentiel ?** L'effet physiologique d'une heure à 90% FC max est disproportionnellement supérieur à 3 heures à 60% FC max. La pondération exponentielle capture cette non-linéarité que le modèle linéaire par zones ignore.

**Pourquoi sex-spécifique ?** Les femmes ont une courbe d'adaptation cardiaque différente aux efforts intenses (réponse catécholaminergique moindre). Banister (1991) a calibré k=1.92 (H) et k=1.67 (F) sur des données de performance athlétique.

#### Normalisation HRSS

Le TRIMP brut n'est pas lisible seul (unité arbitraire). Il est normalisé par rapport à la référence physiologique : **1 heure exactement au LTHR** (Lactate Threshold Heart Rate = seuil lactique ≈ 92% FC max par défaut).

```
HRR_LTHR       = (LTHR − HR_repos) / (HR_max − HR_repos)
TRIMP_1h_LTHR  = 3600 × HRR_LTHR × 0.64 × exp(k × HRR_LTHR)

HRSS = (TRIMP_total / TRIMP_1h_LTHR) × 100
```

**Interprétation** :
| HRSS | Signification |
|------|--------------|
| 100 | Équivalent à 1h exactement au seuil lactique (LTHR) |
| < 100 | Séance sous-seuil (Z1-Z3) — récupération possible |
| 100–150 | Séance seuil modérée — stimulus de qualité |
| > 150 | Séance intense ou longue — récupération > 24h nécessaire |
| 400 | Plafond de sécurité (valeur cap) |

**Propriété linéaire** : à intensité constante, HRSS est strictement proportionnel à la durée. 2h au LTHR → HRSS = 200.

#### Implémentation numpy

```python
hr_clean = np.clip(hr, hr_repos, hr_max)           # invalides → hr_repos
hrr      = (hr_clean - hr_repos) / hr_range        # [0, 1]
dt       = np.maximum(np.diff(t), 0.0)             # intervalles de temps
hrr_end  = hrr[1:]                                 # valeur HR en fin d'intervalle
stress   = dt * hrr_end * 0.64 * np.exp(k * hrr_end)
trimp    = float(np.sum(stress))
```

Vectorisé sur l'intégralité de la série → performant sur 6h+ (≥ 21 600 points).

---

### 2.3 Fallback scalaire — `calc_tss()` (import historique)

Utilisé par `history.py` (import Strava des 49 derniers jours) où les streams ne sont pas disponibles — seule la FC moyenne est connue.

```python
# Si NP + FTP disponibles
IF  = NP / FTP
TSS = (durée_s × NP × IF) / (FTP × 3600) × 100

# Si avg_hr + threshold_hr disponibles (sans streams)
hr_ratio = avg_hr / threshold_hr
TSS = durée_h × hr_ratio² × 100

# Suffer score Strava
TSS = suffer_score × 0.7

# Dernier recours
TSS = durée_h × 50
```

| Zone | TSS/heure (table empirique — utilisée uniquement pour le plan builder) |
|------|------------------------------------------------------------------------|
| Z1 | 20 |
| Z2 | 45 |
| Z3 | 65 |
| Z4 | 85 |
| Z5 | 100 |
| Z6 | 120 |

---

### 2.4 Mode RPE (séance manuelle sans Strava)

```python
RPE_EMOJI_INT_MAP = {"easy": 3, "normal": 5, "hard": 8}

# Dans atl_ctl.py (logging manuel)
TSS = (durée_min / 60) × rpe_int² × 10
```

---

### 2.5 Détection anomalie fatigue — `FatigueAnomaly`

#### Principe

Si le RPE déclaré est **significativement supérieur** à la charge cardiaque théorique, le système détecte une anomalie et applique un multiplicateur de sécurité sur le HRSS.

Le "RPE cardiaque estimé" est dérivé du HRR moyen pondéré par le temps :

```
HRR_moyen = Σ(HRR_i × dt_i) / durée_totale     (pondéré par le temps)
RPE_cardiaque_estimé = HRR_moyen × 10           (projection sur échelle 1-10)

delta = RPE_déclaré − RPE_cardiaque_estimé
```

#### Seuil et multiplicateur

```
Si delta < 3   → Pas d'anomalie  (RPE cohérent avec la FC)
Si delta ≥ 3   → FatigueAnomaly :
    rpe_factor = min(1.10 + (delta − 3) × 0.05,  1.20)
    HRSS_corrigé = HRSS × rpe_factor   (cap : 400)
```

| delta | rpe_factor | Interprétation |
|-------|-----------|----------------|
| 3.0 | 1.10 | Fatigue latente modérée |
| 4.0 | 1.15 | Fatigue significative |
| ≥ 5.0 | 1.20 (cap) | Fatigue importante — récupération prioritaire |

**Exemples déclencheurs** :
- FC de Z2 (140 bpm → RPE cardiaque ≈ 6.4) mais RPE déclaré 10 → delta = 3.6 → anomalie
- FC de Z4 (165 bpm → RPE cardiaque ≈ 8.2) avec RPE déclaré 9 → delta = 0.8 → pas d'anomalie

#### Deux points d'appel

**1. Au moment de l'analyse Strava (webhook)** — `calc_hrss()` avec `user_rpe=None`
- Pas d'anomalie possible (RPE pas encore saisi)
- HRSS calculé sur les streams temps-réel (haute fidélité)

**2. Au moment de la soumission du RPE** — `detect_fatigue_anomaly_scalar()`
- RPE connu → comparaison possible
- `avg_hr` stocké dans `session_log` utilisé à la place des streams
- Approximation suffisante pour la décision binaire (delta ≥ 3 sur 10)

```python
# Dans cb_rpe_strava (session_log.py) :
rpe_int = RPE_EMOJI_INT_MAP[rpe_emoji]          # "hard" → 8
fa = detect_fatigue_anomaly_scalar(
    avg_hr=log.avg_heart_rate,
    hr_rest=hr_rest,
    hr_max=hr_max,
    user_rpe=rpe_int,
    sex=sex or "M",
)
# → FatigueAnomaly ou None → sérialisé en dict → passé au LLM
```

#### Impact sur l'analyse LLM

Si une `FatigueAnomaly` est détectée, son message remplace le `_detect_rpe_mismatch` générique dans le prompt :

```
⚠️ Alerte fatigue : RPE déclaré (8/10) nettement supérieur à la charge
cardiaque estimée (4.2/10). Possible fatigue résiduelle ou état de forme
dégradé — surveiller la récupération.
```

Le LLM reçoit également `rpe_cardiac_estimate` et `rpe_delta` pour contextualiser son interprétation.

---

### 2.6 Baseline TSS hebdomadaire

**Priorité dans `generate_plan()`** :

```python
# 1. CTL Strava disponible → steady-state réel
initial_tss = current_ctl × 7

# 2. Fallback — pas de Strava (onboarding classique)
initial_tss = hours_per_week × 40   # hypothèse Z2 dominant
```

`tss_from_weekly_hours()` reste utilisé pour le seeding CTL quand l'historique Strava est absent.
Le CTL Strava est la source de vérité : `CTL × 7 = TSS hebdomadaire au steady-state EMA.

---

### 2.7 TSS intervalles (calcul composite — plan builder)

```python
WARMUP_MIN     = 15   # échauffement Z1 — Sweet Spot / Threshold
WARMUP_VO2_MIN = 20   # échauffement étendu VO2max (inclut accélérations pré-effort)
COOLDOWN_MIN   = 15   # retour au calme Z1

_interval_tss(sets, work_min, rest_min, zone, coaching_mode, ftp, warmup_min=WARMUP_MIN):
    warmup   = estimate_session_tss("Z1", warmup_min, mode, ftp)
    work     = estimate_session_tss(zone, sets × work_min, mode, ftp)
    rest     = estimate_session_tss("Z1", (sets-1) × rest_min, mode, ftp)
    cooldown = estimate_session_tss("Z1", COOLDOWN_MIN, mode, ftp)
    return warmup + work + rest + cooldown
```

4 segments explicites — aucun temps "perdu". Le TSS couvre l'intégralité de la séance.

Les séances VO2max utilisent `warmup_min=WARMUP_VO2_MIN` (20 min) : l'échauffement plus long
inclut 2-3 accélérations progressives avant les répétitions, indispensables pour la sécurité
et la qualité des premières séries à intensité maximale.

---

## 3. ATL / CTL / TSB (`engine/atl_ctl.py`)

### Formules EMA calendaires

```
k_ATL = 1 − exp(−1/7)   ≈ 0.1331
k_CTL = 1 − exp(−1/42)  ≈ 0.0235

Pour chaque séance sur le jour d (précédée de d_prev) :
  gap = (d − d_prev).days

  1. Déclin pour les jours de repos :
     ATL = ATL × exp(−gap / 7)
     CTL = CTL × exp(−gap / 42)

  2. Ajout du stress :
     ATL += TSS × k_ATL
     CTL += TSS × k_CTL

  3. Mise à jour de d_prev = d
```

**Cas spéciaux :**
- `gap = 0` (deux séances le même jour) → `exp(0) = 1` → pas de décroissance entre elles
- Première séance : `gap = 1` (convention, pas de déclin sur la toute première)

```
TSB = CTL − ATL    (positif = frais, négatif = fatigué)
```

### Interprétation TSB (`tsb_label`)
| TSB | Label | Emoji |
|-----|-------|-------|
| < −30 | Surmenage | 🔴 |
| −30 à 0 | Fatigue normale | 🟡 |
| 0 à +5 | Bonne forme | 🟢 |
| +5 à +15 | Forme de pointe (zone cible affûtage) | ✨ |
| +15 à +20 | Très frais | 🔵 |
| > +20 | Transition — risque de désentraînement | ⚪ |

### CTL seeding (correction historique court)
L'EMA CTL n'a pas convergé avant 84 jours (2 × τ_CTL = 2 × 42j).
Si la fenêtre de données est < 84j, on amorce CTL à l'état d'équilibre estimé :

```python
CTL_initial = weekly_tss / 7    # = TSS moyen journalier = steady-state EMA
```

Appliqué dans `/forme` si `hours_per_week` est disponible dans le profil.
ATL n'est **pas** amorcé (τ=7j → convergence en ~3 semaines, OK avec 49j d'historique Strava).

### Sources de données (`/forme`)
La commande `/forme` combine deux sources **sans double-comptage** :
- **`activities`** avec `activity_date < plan.start_date` → historique Strava pré-plan
- **`session_logs`** → exécution du plan en cours

`compute_fitness_from_any()` accepte les deux types via duck-typing :
- SessionLog → `.tss_actual` / `.logged_date`
- Activity → `.tss` / `.activity_date`

### Projection CTL théorique (`project_fitness_from_plan`)

Simule l'évolution CTL/ATL/TSB en supposant que l'athlète suit le plan **parfaitement**.
Utilise `tss_target` de chaque `SessionSpec` à la place du TSS réel — même formule EMA.

```python
@dataclass
class FitnessProjectionPoint:
    date: date
    ctl: float         # CTL théorique si plan respecté
    atl: float
    tsb: float
    week_number: int
    tss_planned: float # tss_target de la séance

project_fitness_from_plan(plan, initial_ctl, initial_atl=0.0) -> list[FitnessProjectionPoint]
```

**Usage** :
- Afficher la courbe CTL/TSB projetée sur la durée du plan
- Vérifier que le taper amène TSB ∈ [+5, +15] au jour de course
- Référence pour le futur KPI d'adhérence : `ctl_réel − ctl_théorique` sur fenêtre 2 semaines

**Point clé** : le CTL théorique par séance n'est pertinent qu'en fenêtre glissante (2–4 semaines).
Une seule séance décale CTL de ~0.02 × TSS — le signal est lent par construction (τ = 42j).

---

## 4. Périodisation (`engine/periodization.py`)

### Structure des phases
Les plans sont découpés en 4 phases selon la durée totale :

| Durée totale | Phases disponibles |
|-------------|-------------------|
| < 4 semaines | Build + Taper |
| 4-7 semaines | Build + Peak + Taper |
| 8-16 semaines | Base + Build + Peak + Taper |
| > 16 semaines | Base (long) + Build + Peak + Taper |

### Proportions et multiplicateurs TSS (plan 8-16 semaines)
| Phase | Proportion | TSS multiplier (début→fin) | Logique |
|-------|-----------|---------------------------|---------|
| Base | ~40% | 0.65 → 0.80 | Construction aérobie |
| Build | ~35% | 0.80 → 1.00 | Montée en charge progressive |
| Peak | ~15% | 0.95 → 0.95 | Volume maintenu, intensité spécifique |
| Taper | ~10% | 0.55 (fixe) | Seule vraie réduction de charge |

**Rationale peak à 0.95** : le peak maintient une charge proche du build (−5%) avec un contenu
plus intense (davantage de VO2/Z5). La réduction de charge est réservée au taper pour ne pas
perdre de CTL avant la course.

### Règle de récupération par phase
- Le compteur de semaines **se réinitialise à chaque changement de phase**
- Toutes les 4 semaines dans la phase (`phase_week % 4 == 0`) → semaine de récupération
- Semaine de récupération = TSS cible × 0.60
- Peak (≤ 2 semaines) et Taper (1 semaine) : jamais de récup automatique — trop courts
- Le CTL estimé est mis à jour même pendant les semaines de récup (déclin EMA réel)

### Plancher de charge — base/build ≥ volume actuel

Pour éviter que le plan commence en dessous du niveau de l'athlète (ce qui causerait une baisse de CTL) :

```python
if phase.phase in ("base", "build"):
    tss_start = max(tss_start, initial_tss)
    tss_end   = max(tss_end,   initial_tss)
```

**Exemple** : CTL=52 → `initial_tss=364`. Phase base avec `0.55 × peak=300 < 364` → plancher s'applique.
Sans ce plancher, l'athlète commence à 300 TSS/sem (-18%) et CTL baisse pendant toute la phase de base.

### Wave loading intra-bloc (build + peak)

Pattern 3+1 **non linéaire** : les 3 semaines de charge d'un bloc ne sont pas à TSS constant mais suivent `_BLOCK_WAVE = (0.85, 1.00, 1.15)` appliqué à la cible interpolée de la phase.

| Position dans le bloc | Multiplicateur | Rôle |
|-----------------------|---------------|------|
| Semaine 1 (nominal) | ×1.00 | Cible linéaire — entretien CTL au niveau du plan |
| Semaine 2 (charge) | ×1.10 | Surcharge modérée, ATL monte |
| Semaine 3 (surcharge) | ×1.20 (plafonné à `wave_cap`) | Pic de fatigue → TSB −12 à −20 |
| Semaine 4 | ×0.60 (récupération) | Deload → surcompensation |

**Pourquoi pas (0.85, 1.00, 1.15) ?** Un ×0.85 tombe systématiquement sous le TSS d'entretien
du CTL courant (CTL × 7). La semaine "easy" ferait donc BAISSER le CTL, annulant la progression.
**Règle fondamentale : CTL croît si et seulement si TSS_semaine > CTL_courant × 7.**
Les trois semaines de charge sont toutes ≥ ×1.00 → CTL croît ou stagne, jamais ne régresse.

La phase **base** garde une progression linéaire (construction aérobie douce, pas de wave).

### Contrainte de progression — Ramp Rate CTL dynamique

La contrainte de progression dépend du niveau détecté depuis le CTL Strava initial :

| CTL initial | Niveau | Ramp rate max | Max TSS hebdo |
|-------------|--------|---------------|---------------|
| < 35 | Débutant | 4 CTL/sem | `7 × (CTL + 4/K_w)` |
| 35–70 | Intermédiaire | 6 CTL/sem | `7 × (CTL + 6/K_w)` |
| > 70 | Avancé | 8 CTL/sem | `7 × (CTL + 8/K_w)` |

Où `K_w = 1 − exp(−1/6) ≈ 0.1535` (déclin CTL sur 7 jours).

Dérivé de : `ΔCTL/sem = K_w × (TSS_daily − CTL)` → inverser pour obtenir le max TSS.

**Contrainte unique (CTL connu) :** ramp rate CTL — seul vrai garde-fou physiologique.
L'ancien anti-spike fixe `current_tss × 1.15` est supprimé : avec le wave loading, la semaine
easy (×0.85) est intentionnellement basse, rendant le saut vers hard faussement grand en %.

**Fallback** (CTL inconnu, pas de Strava) : `+10%/semaine` sur le TSS. Pas de wave loading
(sans CTL, impossible de valider physiologiquement les pics de charge).

### TSS peak cible
```python
compute_peak_tss(initial_tss, level, structured_history) -> float
```

| Niveau | Historique structuré | Multiplicateur | Plafond TSS/semaine |
|--------|---------------------|---------------|---------------------|
| beginner | Non | ×1.6 | 350 |
| beginner | Oui | ×1.8 | 450 |
| intermediate | Non | ×1.5 | 600 |
| intermediate | Oui | ×1.5 | 650 |
| advanced | Non | ×1.60 | 900 |
| advanced | Oui | ×1.60 | 950 |
| expert | Non | ×1.2 | 950 |
| expert | Oui | ×1.2 | 1050 |

**Rationale advanced ×1.60** : un athlète 4+ W/kg avec 10h/semaine doit pouvoir atteindre CTL 65-70.
Le multiplicateur ×1.60 avec fill Z3 produit ~550-600 TSS/sem en pic → TSB −20 à −25, CTL pic ~65.

### wave_cap — plafond supérieur du wave

Pour les niveaux `advanced` et `expert`, le plafond du wave est `peak_tss × 1.30` au lieu de `peak_tss`.
Permet à la semaine de surcharge (×1.20) de dépasser `peak_tss` cible sans être bloquée.

```python
wave_cap = peak_tss * 1.30 if level in ("advanced", "expert") else peak_tss
```

### Continuité charge_week cross-phase

Le compteur `charge_week` (position dans le wave) **ne se réinitialise pas** aux frontières de phases.
Sans cela, la première semaine de chaque phase est toujours ×1.00 → la semaine ×1.20 ne peut jamais
tomber sur la première semaine d'une phase. Le compteur est global sur tout le plan.

---

## 5. Génération du plan (`engine/plan_builder.py`)

### Entrées / Sorties
- **Entrée** : `AthleteProfileSchema` (FTP, zones power/HR, objectif, date cible, jours disponibles, niveau, coaching_mode)
- **Sortie** : `TrainingPlanSchema` → liste de `WeekPlan` → liste de `SessionSpec`

### Contrainte haute intensité inter-séances

```python
HIGH_INTENSITY_ZONES = {"Z3", "Z4", "Z5"}
```

Toute séance `intervals` en Z3/Z4/Z5 est soumise à la règle **gap ≥ 2 jours** avec les autres
séances haute intensité et la sortie longue. Z3 (Sweet Spot, 88-93% FTP) est inclus car son
stimulus de récupération est comparable à un threshold court.

### Structures d'intervalles (rotation sur semaine_dans_bloc % 3)

**Sweet Spot (Z3)** :
| Rotation | Structure | Repos |
|----------|-----------|-------|
| 0 | 2×15min | 5min |
| 1 | 2×20min | 5min |
| 2 | 3×15min | 5min |

**Threshold (Z4)** :
| Rotation | Structure | Repos |
|----------|-----------|-------|
| 0 | 3×12min | 4min |
| 1 | 4×10min | 3min |
| 2 | 2×20min | 5min |

**VO2 Max (Z5)** — échauffement 20 min (vs 15 min pour les autres) :
| Rotation | Structure | Repos |
|----------|-----------|-------|
| 0 | 5×5min | 3min |
| 1 | 6×5min | 3min |
| 2 | 4×6min | 3min |

La rotation est `week_in_block % len(structures)` — pas liée aux semaines de récupération.

### Description des séances (`_session_description`)
```python
ZONE_NAMES = {
    "Z1": "Récupération active",
    "Z2": "Endurance",
    "Z3": "Tempo",
    "Z4": "Seuil lactique",
    "Z5": "VO2 Max",
    "Z6": "Anaérobie",
}
```

Format : `"Intervalles Z4 (Seuil lactique) — 3×8min"`
Le nom de zone est dérivé de `ZONE_NAMES`, jamais hardcodé dans le label de structure.

**Cas particulier Z5/Z6 en mode HR** : la fréquence cardiaque est inutilisable sur les efforts
courts à haute intensité (inertie cardiaque : 2+ min pour atteindre la zone cible). Si
`coaching_mode == "hr"` et `zone in ("Z5", "Z6")`, la description ajoute automatiquement :
> *(Pilotage au RPE 9/10 — le cardio monte trop lentement sur ces intervalles courts ; suivre la sensation d'effort, pas la FC)*

`_session_description(wtype, zone, detail, coaching_mode="hr")` — paramètre  propagé
depuis `_build_sessions` jusqu'à chaque `SessionSpec.description_fr`.

### Plafond des séances fill (endurance / récupération)

Les séances `endurance` et `recovery` reçoivent un budget TSS résiduel (`tss_target - fixed_tss`)
pondéré (endurance ×1.0, recovery ×0.4). Sans plafond, un gros budget TSS génère des durées
aberrantes via `_tss_to_duration` (Z1 = 20 TSS/h → 40 TSS → **120 min** de récupération active).

Plafonds appliqués dans `_build_sessions` après calcul de durée, avec recalcul du TSS cohérent :

| Type | Plafond durée | Raison |
|------|--------------|--------|
| `recovery` (Z1) | 60 min | Au-delà : fatigue sans adaptation, pas de récupération |
| `endurance` (Z2) | 150 min | Long rides structurés séparément ; 2h30 max pour endurance de remplissage |

### Séances types par phase
| Phase | Séances principales |
|-------|---------------------|
| Base | Sortie longue Z2, Endurance Z2, Récupération Z1 |
| Build | Sweet Spot Z3, Threshold Z4, Long ride Z2 |
| Peak | VO2 Max Z5, Threshold Z4, Long ride Z2 |
| Taper | Durées réduites, activation Z3, récupération Z1 |
| Race Week | Z2 120min (activation), Z5 5×5min (rappels neuromusculaires), marqueur course |

### Fill Z3 pour les niveaux advanced/expert (build + peak)

En semaines de charge pour les niveaux `advanced` et `expert`, la **plus grande séance d'endurance**
(fill résiduel) est upgradée de Z2 (45 TSS/h) en Z3 (65 TSS/h). Même durée, ~+80 TSS sur 4h de session.

```python
if level in ("advanced", "expert") and phase in ("build", "peak"):
    biggest_fill = max(endurance_fills, key=lambda s: s.tss_planned)
    biggest_fill.zone_code = "Z3"
```

Justification : un athlète 4+ W/kg accumule trop peu de charge en Z2 pur pour progresser —
le Tempo/Sweet Spot au même volume horaire est plus stimulant.

### Récupération dynamique (vs fixe 60min)

Avant : semaines de récup → 3× Z2 60min fixe ≈ 130 TSS livrés quelle que soit la cible.
Après : la durée des séances Z2 est calculée depuis `tss_target` via `_tss_to_duration`, plafonnée à 150min.
Résultat : ~265 TSS livrés (71% de maintien CTL) vs 35% avant.

### Race Week

Ajoutée automatiquement si `(target_date − plan_last_day).days ≥ 3`.

Structure visant TSB +10/+15 le jour de la course :
- **J3 (ex. mardi)** : Z2 120min — 90 TSS — maintien du tonus musculaire
- **J5 (ex. jeudi)** : Z5 5×5min + 20min Z2 échauffement + 10min récup — ~64 TSS — rappels neuromusculaires
- **Jour J (ex. samedi)** : marqueur 20min Z2 — 15 TSS — placeholder pour la course réelle Strava

Math ATL (τ=7j) avec ATL≈63 en entrée S11 :
```
Lun → ATL 54.9 (déclin)
Mar +90 → ATL 59.6
Jeu +64 → ATL 60.4
Ven → ATL 52.4
Sam +15 course → ATL 41.4 ; CTL≈55 → TSB≈+13 ✓
```

### Budget heures — facteur par niveau

```python
budget_factor = 1.10 if level in ("advanced", "expert") else 1.05
```

Advanced : jusqu'à 11h/semaine (110% × 10h) avant déclenchement du garde-fou.

---

## 6. Modification du plan (`engine/plan_modifier.py`)

Le moteur expose deux niveaux de granularité : **semaine entière** et **séance précise**. Dans les deux cas, la modification est proposée (non destructive) et doit être confirmée par l'utilisateur via les boutons inline du bot, sauf pour les blessures (appliquées immédiatement).

---

### 6.1 Modification semaine entière

`propose_week_adjustment(plan, week_offset, modification_type)` :
- Ne modifie **pas** le plan — retourne une proposition avec `requires_confirmation: True`
- Le handler `chat.py` stocke la proposition dans `pending_proposal`
- L'utilisateur confirme avec un bouton inline → `apply_proposed_modification()`

**Types de modification et facteurs** :
| Type | Facteur TSS | Effet |
|------|-------------|-------|
| `reduce_intensity` | −30% (×0.70) | Zones réduites d'un cran |
| `reduce_volume` | −40% (×0.60) | Durées raccourcies |
| `skip_session` | ×0.0 | Sessions Z4-Z6 supprimées |
| `swap_to_recovery` | −50% (×0.50) | Tout converti en Z2 |

`_downgrade_zone(zone)` : Z6→Z5→Z4→Z3→Z2→Z1 (réduit d'un cran).

**Compensation de durée lors d'un downgrade** — baisser une zone réduit le stimulus, le moteur compense par +15% de durée :
```
_ZONE_DOWNGRADE_DURATION_FACTOR = 1.15
```
Exemple : séance Z5 60min dégradée en Z4 → 60 × 0.70 × 1.15 ≈ 48min.

**Reprise progressive automatique** — si `week_offset=0` (semaine courante), les 2 semaines suivantes sont adaptées :
- S+1 : ×0.80 (zones dégradées d'un cran)
- S+2 : ×0.90 (zones originales)

---

### 6.2 Ajustement séance unique (contrainte vie quotidienne)

`propose_session_adjustment(plan, target_date, action, available_days)` :
- Cible **une seule séance** identifiée par sa date exacte
- Ne modifie **pas** le plan — retourne une proposition avec `requires_confirmation: True`
- Retourne `{"no_session": True, ...}` si aucune séance ce jour-là (le LLM répond en conséquence)

**Actions disponibles** :
| Action | Effet | Cas d'usage typique |
|--------|-------|---------------------|
| `skip` | Supprime la séance | Réunion, séance Z1/Z2 non critique |
| `shift` | Déplace au prochain créneau libre | Réunion, séance intensive à préserver |
| `reduce_50` | TSS ×0.50, durée ×0.65 (min 20min) | Fatigue du jour, coup de mou |
| `indoor` | Ajoute "🏠 Intérieur" dans description, charge inchangée | Météo dangereuse, sortie impossible |

**Algorithme `shift`** — cherche le prochain créneau dans les 7 jours suivants :
1. Parcourt les jours j+1 à j+7
2. Sélectionne le premier jour qui est dans `preferred_days` ET sans séance déjà planifiée
3. Si débordement sur la semaine suivante → la séance est insérée dans la bonne `WeekPlan`
4. Si aucun créneau trouvé → retourne `no_session: True` avec message explicatif

**Identification de la séance** : `week_num = (target_date − plan.start_date).days // 7 + 1`, `dow = target_date.weekday()`

`apply_session_adjustment(plan, proposal)` — dispatché par le callback `cb_apply_modification` quand `proposal["type"] == "session_adjustment"` :
- `skip` : retire la `SessionSpec` de `week.sessions`
- `reduce_50` : remplace la `SessionSpec` par la version réduite
- `indoor` : met à jour uniquement `description_fr`
- `shift` : retire du jour d'origine, insère dans la semaine cible (même ou suivante), sessions re-triées par `day_of_week`

**Identification de la proposition** : le champ `proposal["type"] = "session_adjustment"` distingue les deux types dans le callback → dispatch vers la bonne fonction `apply_*`.

---

### 6.3 Adaptation blessure (effet immédiat, sans confirmation)

`adapt_plan_for_injury(plan, injury_data)` — appliqué immédiatement par `update_injury_status` :

**Restrictions de zones par sévérité** :
| Sévérité | Zones restreintes → Remplacement |
|---------|----------------------------------|
| `severe` | Z3, Z4, Z5, Z6 → Z1 |
| `moderate` | Z4, Z5, Z6 → Z2 |
| `mild` | Z5, Z6 → Z3 |

**Facteurs de reprise progressive** :
| Semaine | Facteur TSS | Facteur durée |
|---------|-------------|---------------|
| S0 (blessure) | ×0.50 | ×0.50 |
| S+1 | ×0.70 | ×0.70 |
| S+2 | ×0.90 | ×0.90 |

---

## 7. Import Strava (`strava/history.py`)

### Appels API
```
get_athlete()          → sexe, poids, pays, athlete_id
get_athlete_stats()    → totaux YTD (distance, dénivelé, activités)
get_activities(weeks=7) → activités des 49 derniers jours
```

### Calcul TSS par activité
Pour chaque activité Strava :
1. Si `device_watts=True` : TSS power (durée × IF² × 100 avec IF estimé depuis NP/FTP)
2. Sinon si `has_heartrate=True` : TSS HR (durée × TSS/h zone FC)
3. Sinon si `suffer_score` disponible : `suffer_score × 0.7`
4. Fallback : `50 TSS/heure`

### Auto-détection niveau (`_analyze`)
- TSS hebdo moyen calculé sur la fenêtre importée
- Seuils : beginner < 150, intermediate 150-350, advanced 350-600, expert > 600
- FTP détecté depuis `best_20min_power` Strava si disponible
- FC max détectée depuis `max_heartrate` dans les activités

### Résultat `analysis`
```python
{
    "level_detected": "intermediate",
    "volume_suggested": 8.5,      # heures/semaine estimées
    "has_power_meter": True,
    "ftp_detected": 245,           # None si absent
    "hr_max_detected": 182,        # None si absent
    "athlete_sex": "M",
    "athlete_weight_kg": 72.0,
    "preferred_days": [1, 3, 5],  # jours semaine (0=lundi)
}
```

---

## 8. Onboarding FSM

### Flow Strava (court — 5 questions après import)
```
/start → STRAVA_PIVOT
    ↓ [Connecter Strava]
OAuth Strava → import 49j → generate_strava_intro() (LLM narratif)
    ↓ [Continuer l'onboarding]
STRAVA_SHORT_GOAL   → objectif (compétition/sportif/santé/loisir)
STRAVA_SHORT_DATE   → date cible (ou "pas de date")
STRAVA_SHORT_DAYS   → nombre de jours/semaine disponibles
STRAVA_SHORT_HEALTH → contraintes santé / blessures
    ↓
DISCLAIMER → acceptation
    ↓
Génération plan + onboarding_completed = True
```

**Auto-détecté depuis Strava** : level, FTP, FC max, power_meter, sexe, poids.
**Toujours demandé** : objectif, date cible, jours disponibles, contraintes santé.
`hours_per_week` est demandé à l'étape STRAVA_SHORT_DAYS (pas auto-rempli).

### Flow classique (12 étapes)
```
STEP_0_GOAL → STEP_1_DATE → STEP_2_LEVEL → STEP_3_AVAILABILITY →
STEP_4_DAYS → STEP_5_FTP → STEP_6_HR → STEP_7_STYLE →
STEP_8_HEALTH → STEP_9_INJURIES → STEP_10_EQUIPMENT → PREVIEW →
DISCLAIMER → génération plan
```

### Données persistées dans onboarding_state.session_data (JSONB)
```json
{
  "goal": "competition",
  "target_date": "2026-08-15",
  "level": "intermediate",
  "hours_per_week": 10,
  "preferred_days": [1, 3, 5, 6],
  "ftp": 245,
  "ftp_source": "declared",
  "hr_max": 182,
  "hr_max_source": "declared",
  "hr_rest": 55,
  "power_meter": true,
  "strava_analysis": { ... }
}
```

---

## 9. Chat LLM orchestration (`llm/chat.py`)

### Flux complet
```
Message utilisateur
    ↓
run_chat(user_message, user, session)
    ↓ charge contexte
    ├── profil AthleteProfileSchema
    ├── plan TrainingPlanSchema (depuis plan_technical JSONB)
    ├── logs session_logs (tous)
    └── historique 15 derniers messages chat_messages
    ↓ build_system_prompt()
    ↓ build_context_messages() → format messages OpenAI
    ↓ run_agentic_loop(system, messages, tools, tool_executor)
    ↓
retourne (response_text, intent, tool_used, pending_proposal)
```

### Outils LLM (TOOL_DEFINITIONS)

**get_fitness_data**
- Aucun paramètre
- Calcule ATL/CTL/TSB depuis `session_logs` (status="done")
- Retourne : `{atl, ctl, tsb, tsb_label, recent_logs[7]}`

**get_upcoming_sessions** (paramètre : `days: int = 7`)
- Parcourt `plan.plan_technical.weeks` pour les N prochains jours
- Retourne : `{sessions: [{date, day, workout_type, zone, duration_minutes, tss_target, description_fr, week_number, phase}]}`

**update_injury_status** (paramètres : `location`, `severity`, `estimated_recovery_days`)
- Enregistre la blessure dans `athlete_profiles.profile.injury`
- Appelle `adapt_plan_for_injury()` → modifie `plan.plan_technical` immédiatement
- Retourne confirmation + semaines adaptées

**propose_plan_modification** (paramètres : `reason`, `modification_type`, `week_offset`)
- Réservé aux contraintes affectant **une semaine entière**
- Appelle `propose_week_adjustment()` — ne modifie PAS le plan
- Retourne proposition avec `requires_confirmation: True`
- Le handler chat stocke en FSM → affiche boutons Apply/Cancel

**propose_session_adjustment** (paramètres : `constraint_type`, `day_offset`, `action`)
- Réservé aux contraintes affectant **un seul jour** (réunion, météo, fatigue du jour)
- `day_offset` : 0=aujourd'hui, 1=demain, …6
- `constraint_type` ∈ `{meeting, weather_bad, tired_today, personal}`
- `action` ∈ `{skip, shift, reduce_50, indoor}`
- Calcule `target_date = today + timedelta(day_offset)`, passe `profile.availability.preferred_days` au moteur
- Retourne `{no_session: True, message}` si aucune séance ce jour → le LLM répond sans afficher de boutons
- Sinon retourne proposition `{type: "session_adjustment", ...}` → FSM + boutons Apply/Cancel

### Routing LLM — règles injectées dans `build_system_prompt()`
```
- Contrainte UN SEUL JOUR → propose_session_adjustment
- Contrainte UNE SEMAINE ENTIÈRE → propose_plan_modification
- météo + peut rouler dedans → action=indoor ; sinon → shift ou skip
- fatigue du jour + TSB déjà très négatif → skip ; sinon → reduce_50
- réunion + séance Z1/Z2 → skip ; séance intensive → shift
```

### Intent (déduit post-hoc depuis l'outil appelé)
| Outil | Intent |
|-------|--------|
| `get_fitness_data` | `question` |
| `get_upcoming_sessions` | `question` |
| `update_injury_status` | `injury_report` |
| `propose_plan_modification` | `plan_modification` |
| `propose_session_adjustment` | `plan_modification` |
| (aucun outil) | `other` |

### Historique conversation
- 15 derniers messages stockés dans `chat_messages` (DB)
- Passés dans `messages[]` au LLM via `build_context_messages()`
- Le system prompt rappelle explicitement au LLM qu'il a accès à cet historique

---

## 10. Commande /forme (`bot/routers/forme.py`)

### Algorithme complet
```
1. Récupérer plan actif → plan_start (ou date du jour)
2. Récupérer activities (365 jours) → filtrer pre_plan_acts (< plan_start)
3. Récupérer session_logs (tous)
4. all_items = pre_plan_acts + session_logs

5. CTL seeding :
   - Si fenêtre < 84j ET hours_per_week dispo dans profil
   → initial_ctl = weekly_tss / 7  (weekly_tss = hours_per_week × 40)
   → seed_date = date.today() - timedelta(days=49)

6. compute_fitness_from_any(all_items, initial_ctl, seed_date)
   → EMA démarre à T-49, gap réel jusqu'à la première activité
   → FitnessMetrics(atl, ctl, tsb)

7. Affichage tableau 7 dernières séances (icon + date + TSS)

8. Appel LLM _generate_fitness_interpretation :
   - context_line adapté (Strava pré-plan OU assiduité plan)
   - 7 séances récentes avec date + TSS réels
   - Fallback déterministe si LLM indisponible
```

### Prompt LLM /forme — zones d'interprétation
| Zone | TSB | Interprétation |
|------|-----|----------------|
| ROUGE | < −30 | Fatigue critique — "dans le dur" |
| TRAVAIL | −25 à −10 | Construction de la puissance — progression |
| GRISE | −5 à +5 | Entraînement "pépère" — pas assez de stimulus |
| AFFÛTAGE | > +10 | Batteries chargées — prêt pour la performance |

Consigne : pas d'acronymes CTL/ATL/TSB dans la réponse, termes imagés uniquement.
Format : 3 phrases (diagnostic / historique récent / conseil tactique).

---

## Schémas Pydantic clés (`engine/schemas.py`)

```
AthleteProfileSchema
├── level: "beginner" | "intermediate" | "advanced" | "expert"
├── goal: "competition" | "sportif" | "sante" | "loisir"
├── ftp: int | None
├── hr_max: int | None
├── hr_rest: int
├── hours_per_week: float
├── preferred_days: list[int]   (0=lundi, 6=dimanche)
├── power_meter: bool
├── coaching_mode: "power" | "hr"
├── availability: {hours_per_week, preferred_days}
└── injury: {is_injured, location, severity, zone_restrictions, ...} | None

TrainingPlanSchema
├── goal: str
├── level: str
├── total_weeks: int
├── start_date: date | None
└── weeks: list[WeekPlan]

WeekPlan
├── week_number: int
├── phase: "base" | "build" | "peak" | "taper"
├── is_recovery_week: bool
├── total_tss_target: float
├── start_date: date | None
└── sessions: list[SessionSpec]

SessionSpec
├── day_of_week: int   (0=lundi)
├── workout_type: "long_ride" | "intervals" | "endurance" | "recovery"
├── zone_code: "Z1" … "Z6"
├── duration_minutes: int
├── tss_target: float
└── description_fr: str
```

---

## 11. Récap hebdomadaire (`services/weekly_recap.py` + `bot/routers/recap.py`)

### Vue d'ensemble

Le bilan hebdomadaire est envoyé en **3 messages Telegram séquentiels**, chacun avec un rôle distinct :

```
Message 1 — 📊 Stats (déterministe)
  Semaine du {lundi} au {dimanche}
  TSS réalisé / moy 6 semaines / tendance
  Séances réalisées / prévues / % compliance
  [alerte monotonie si indice > 2.0]

Message 2 — 🧠 Analyse coach (LLM)
  Observation ancrée dans les chiffres
  Ton adapté à la qualité de la semaine

Message 3 — 🎯 Semaine prochaine (LLM)
  Commentaire du programme planifié (sessions réelles issues du moteur)
  Conseil tactique selon l'état de forme actuel
```

Le LLM **ne calcule rien** — il reçoit les chiffres pré-calculés par le moteur et les interprète.

### Déclenchement

| Mode | Mécanisme |
|------|-----------|
| Automatique | Scheduler asyncio dans `main.py` — dimanche 20h00 UTC, tous les utilisateurs actifs |
| Sur demande | Commande `/recap` — utilisateur courant, semaine en cours |

### Sources de données (sans double-comptage)

Pattern identique à `/forme` :
- `pre_plan_acts` = activités Strava avec `activity_date < plan.start_date` → historique pré-plan
- `logs` = `session_logs` → exécution du plan en cours
- `all_items = pre_plan_acts + logs`

### Métriques calculées

| Métrique | Source | Usage |
|----------|--------|-------|
| `tss_7d` | `compute_weekly_snapshot(all_items, today)` | Stats section |
| `tss_6w_avg` | idem | Stats section |
| `load_trend_pct` | idem | Stats section + directive LLM |
| `sessions_done_7d` | idem (compte uniquement `SessionLog.status="done"`) | Compliance |
| `monotony_index` | idem | Alerte si > 2.0 |
| `fitness.tsb` | `compute_fitness_from_any(all_items)` | Directive tonalité LLM |
| `sessions_planned` | `len(current_week.sessions)` depuis plan | Compliance |
| `compliance_pct` | `sessions_done / sessions_planned × 100` | Stats + LLM |
| `next_week.sessions` | Plan semaine N+1 | Template LLM semaine suivante |

**CTL seeding** : même logique que `/forme` — si historique < 84j et `hours_per_week` disponible dans le profil, amorçage CTL depuis `weekly_tss / 7`.

### Directive tonalité (pure function)

`_recap_tone_directive(snapshot, tsb, compliance_pct)` — injectée dans le prompt LLM pour piloter le ton sans que le LLM le décide lui-même :

| Condition | Directive |
|-----------|-----------|
| `tsb < -30` ou `compliance < 30%` | direct et protecteur — signal d'alarme, suggère repos sans culpabiliser |
| `trend > 20%` et `compliance ≥ 80%` | célébratoire — semaine remarquable, souligne la progression |
| `compliance ≥ 80%` et `tsb > -10` | enthousiaste et encourageant — belle semaine, maintenir le cap |
| `compliance < 50%` | compréhensif et factuel — rappelle que chaque séance compte |
| sinon | équilibré — coaching factuel et motivant |

### Prompts LLM (`app/llm/prompts.py`)

Trois constantes dédiées :

- **`WEEKLY_RECAP_SYSTEM_PROMPT`** — règles absolues du coach (pas de recalcul, ton adapté, français, emojis sobres)
- **`WEEKLY_RECAP_COACH_TEMPLATE`** — data context pour l'analyse coach. Blocs explicitement séparés : `[PROFIL ATHLÈTE]`, `[MÉTRIQUES PHYSIOLOGIQUES]`, `[CHARGE SEMAINE]`, `[FORME — usage coach uniquement]`. La FTP est dans le bloc physiologique, jamais dans le bloc objectif, pour éviter toute confusion sémantique.
- **`WEEKLY_RECAP_NEXTWEEK_TEMPLATE`** — inclut les séances réelles de la semaine suivante (depuis `next_week.sessions`) avec `description_fr`, `zone_code`, `duration_minutes`, `tss_target`. Le LLM commente le programme planifié par le moteur, il ne le remplace pas.

Deux appels LLM distincts : `max_tokens=200` pour l'analyse coach, `max_tokens=150` pour les recommandations. Chacun a son fallback déterministe si le LLM est indisponible.

### Fallbacks déterministes

| Section | Critère | Réponse |
|---------|---------|---------|
| Analyse coach | `compliance < 30%` | Message protecteur, reprise progressive |
| Analyse coach | `trend > 20%` et `compliance ≥ 80%` | Célébration de la belle semaine |
| Analyse coach | `compliance ≥ 80%` | Validation de l'assiduité |
| Analyse coach | sinon | Message d'encouragement générique |
| Semaine suivante | `tsb < -10` | Recommande prudence et écoute du corps |
| Semaine suivante | `tsb > 10` | Recommande d'attaquer les séances clés |
| Semaine suivante | sinon | Recommande régularité et respect des intensités |

### Résultat

```python
@dataclass
class WeeklyRecapResult:
    stats_section: str       # HTML déterministe — safe à envoyer avec parse_mode="HTML"
    coach_section: str       # LLM ou fallback — doit passer par html.escape() avant envoi
    next_week_section: str   # LLM ou fallback — doit passer par html.escape() avant envoi
    has_data: bool           # False si aucun log/activité → message "pas de données"
```

**Règle HTML** : `stats_section` contient des balises HTML construites manuellement (safe). `coach_section` et `next_week_section` sont des sorties LLM → `html.escape()` obligatoire (pattern identique à `forme.py:99`).

---

## 12. KPI d'adhérence (`engine/adherence_kpi.py`)

### Philosophie

Score gamifié **0 → 100** sur la durée totale du plan. L'athlète part de 0 au premier jour et vise 100 au jour de la course. Principe : récompenser les comportements positifs, ne jamais pénaliser l'absence. Seule exception : la surcharge (sur-entraînement TSB) génère une contribution négative.

### Formule par séance

```
budget_séance = (tss_planifiée / tss_semaine) × (100 / N_semaines)

score = tss_ratio × zone_mult × (1 + bonus)
      plafonné à 1.50

pts = budget_séance × score
```

**Composantes** :

| Variable | Valeur | Condition |
|----------|--------|-----------|
| `tss_ratio` | `min(tss_actual / tss_planned, 1.20)` | volume compliance, max 1.2× |
| `zone_mult` | 1.00 | zones Strava correctes |
| | 0.85 | RPE uniquement (pas de données Strava) |
| | 0.75 | zones incorrectes (ex: endurance prévu, intervals réalisé) |
| `bonus` | +0.10 | `long_ride` planifié réalisé (tss_ratio ≥ 85%) |
| | +0.15 | `intervals` planifié ET `session_type_real == "intervals"` |

### Sur-entraînement — contribution négative

Seul cas où `pts < 0`. Déclenché quand `tsb_after < seuil_niveau` :

```python
excess = min(abs(tsb_after - threshold) / 15.0, 1.0)
pts    = -budget_séance × 0.50 × excess
```

Proportionnel à la profondeur du dépassement, plafonné à `-budget_séance × 0.50`.

| Niveau | Seuil TSB |
|--------|-----------|
| beginner | −18 |
| intermediate | −22 |
| advanced | −28 |
| expert | −32 |

### Propriété de linéarité

Avec des semaines à poids égaux (`100 / N`) et un score moyen de 1.0 :

```
KPI à mi-plan ≈ 50   (±1 selon répartition des bonus)
```

Garantie par la normalisation : budget_semaine × N_semaines = 100, indépendamment du TSS hebdo.

### Rythme idéal et messages

```python
ideal_pts = (week_number / weeks_total) * 100
delta     = cumulative - ideal_pts
```

| Écart vs idéal | Message affiché |
|----------------|-----------------|
| ≥ +5 pts | "⚡ +X pts d'avance sur le rythme parfait" |
| 0 à +5 | "✅ Dans les temps — continue comme ça" |
| −5 à 0 | "🎯 À X pts du rythme — ta prochaine [séance clé] peut tout changer" |
| −15 à −5 | "💪 X pts sous le rythme — ton prochain [séance clé] est ta priorité" |
| < −15 | "🔄 Reste régulier — le score se reconstruit séance après séance" |

**Règle fondamentale** : ne jamais prescrire de séances supplémentaires — pointer uniquement vers la prochaine séance clé déjà planifiée (long_ride ou intervals). Au-delà de 15 pts de retard, encourager la régularité sans créer de pression.

### Milestones (franchissement unique)

| Seuil | Message |
|-------|---------|
| 25 pts | 🌱 "Premier quart bouclé — la machine est lancée !" |
| 50 pts | 🔥 "Mi-chemin atteint — tu tiens le rythme parfait !" |
| 75 pts | 🏆 "Dernière ligne droite — le jour J approche !" |

Détecté par comparaison `prev_cumulative < seuil ≤ cumulative` — sans stockage DB (calcul pur).

### Stockage et calcul

- `session_logs.kpi_contribution FLOAT` (migration 014) — `NULL` si non calculé
- Cumulatif = `SUM(kpi_contribution)` dynamique — jamais stocké (pas de cache à invalider)
- Calculé au moment du log : webhook Strava (`handle_activity_event`) + RPE manuel (`cb_rpe_manual`)
- `tsb_after` = `fitness_metrics.tsb` calculé APRÈS création du log (proxy post-séance, précision suffisante)

### Affichage Telegram

Bloc HTML ajouté à la fin du message d'analyse LLM (edit final post-RPE) :

```
📊 +2.3 pts · sortie longue ✓ · zones ✓
▓▓▓▓▓▓▓░░░░░░░░░░░░░  34.0 / 100
⚡ +4.0 pts d'avance sur le rythme parfait
```

Avec milestone au franchissement :
```
📊 +1.8 pts · fractionné ✓ · zones ✓

🔥 Mi-chemin atteint — tu tiens le rythme parfait !

▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░  51.0 / 100
✅ Dans les temps — continue comme ça
```

Progress bar : `▓` × filled + `░` × (20 - filled), où `filled = round(score/100 × 20)`.
