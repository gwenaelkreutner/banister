# Système d'évaluation offline — Banister

Outil d'évaluation automatisée de la qualité des plans d'entraînement générés par le moteur
déterministe de Banister. Permet de détecter les régressions et d'itérer sur le générateur
avant déploiement.

---

## 1. Vue d'ensemble

Le système fonctionne en deux niveaux complémentaires :

**Vérifications déterministes** — règles objectives, 100% reproductibles :
- Gap insuffisant entre séances intensives (Z4+)
- Spike TSS hebdomadaire > 10%
- Séances hors des jours disponibles
- Volume dépassant la disponibilité déclarée
- etc. (14 règles au total — voir `eval/rules/checker.py`)

**Évaluation LLM (juge)** — jugement qualitatif en 2 passes :
- **Pass 1 (Critic)** : Analyse le plan et produit un scorecard JSON sur 5 dimensions
- **Pass 2 (Verifier)** : Valide ou corrige la critique du premier LLM pour éviter les biais

Les deux niveaux sont combinés dans un rapport JSON de synthèse incluant clusters d'erreurs
et statistiques agrégées.

### Architecture

```
eval/
├── runner.py              # CLI d'orchestration
├── config.py              # Pondérations, seuils, config LLM
├── aggregator.py          # Statistiques de synthèse
├── profiles/
│   ├── matrix.yaml        # ~65 profils systématiques
│   └── edge_cases.yaml    # 5 cas limites
├── rules/
│   └── checker.py         # 14 règles déterministes
└── llm/
    ├── judge.py            # 2-pass critic + verifier
    └── prompts.py          # Prompts structurés JSON-only
```

---

## 2. Prérequis

### Variables d'environnement (dans `.env` à la racine du projet)

Le runner lit le même `.env` que l'application principale. Seules les variables LLM sont nécessaires :

```env
# Choisir l'un des deux providers :

# Option A — Anthropic (recommandé pour la qualité)
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...

# Option B — OpenRouter (moins cher pour tests en volume)
LLM_PROVIDER=openrouter
LLM_MODEL=arcee-ai/trinity-large-preview:free
OPENROUTER_API_KEY=sk-or-...
```

Les autres variables (Telegram, database, Strava) ne sont **pas requises** pour l'éval.

### Dépendances

Aucune dépendance supplémentaire — toutes déjà dans `pyproject.toml` :
```bash
# Si ce n'est pas encore fait :
pip install -e ".[dev]"
# ou avec uv :
uv sync
```

La bibliothèque `pyyaml` est nécessaire pour lire les profils YAML :
```bash
pip install pyyaml
```

---

## 3. Lancement rapide

```bash
# Depuis la racine du projet :

# Smoke test — 5 edge cases, sans LLM, verbose
python -m eval.runner --profiles edge_cases --skip-llm --verbose

# Vérifications déterministes sur toute la matrice (rapide, ~10 sec)
python -m eval.runner --skip-llm

# Évaluation complète — 5 edge cases avec LLM (15-30 sec selon provider)
python -m eval.runner --profiles edge_cases --verbose

# Évaluation complète — toute la matrice (peut prendre plusieurs minutes)
python -m eval.runner --output eval/results/baseline.json

# Rapport sur la matrice systématique uniquement
python -m eval.runner --profiles matrix --max-concurrent 5
```

### Exemple de sortie console

```
[eval] 70 profil(s) chargé(s) — source : all
[eval] 70 plan(s) générés avec succès
[eval] Vérifications déterministes en cours...
  → 3 violation(s) détectée(s)
[eval] Évaluations LLM en cours (3 max en parallèle)...
  [intermediate_power_12w_6h_cyclosportive] ✓ score=7.8
  [edge_expert_plateau] ✓ score=6.2
  ...
  → 70 évaluations LLM (0 erreur(s))

────────────────────────────────────────────────────────────
RÉSUMÉ
────────────────────────────────────────────────────────────
  Profils évalués     : 70
  Évalués par LLM     : 70
  Score moyen         : 7.42 / 10
  Écart-type          : 1.31
  Plans critiques     : 2.9%
  Violations déterm.  : 3
  Plans avec critical : 2

  TOP CLUSTERS D'ERREURS :
    [warning] tss_spike_week → 2 plan(s)
    [llm] securite_recuperation < 6.0 → 8 plan(s)

  Rapport sauvegardé : eval/results/eval_2026-03-20_143022.json
────────────────────────────────────────────────────────────
```

---

## 4. Interpréter les résultats JSON

### Structure du rapport

```json
{
  "generated_at": "2026-03-20T14:30:22Z",
  "engine": "banister",
  "summary": { ... },
  "evaluations": [ ... ]
}
```

### `summary` — lecture rapide

| Champ | Description |
|-------|-------------|
| `mean_composite_score` | Score moyen sur 10 — objectif : > 7.5 |
| `std_composite_score` | Écart-type — > 2 indique une forte hétérogénéité |
| `score_distribution` | Plans répartis en 4 buckets (critical / concerning / acceptable / excellent) |
| `critical_plans_pct` | % de plans avec score composite < 4 — objectif : 0% |
| `by_dimension` | Moyenne min/max par critère LLM — identifie la dimension la plus faible |
| `error_clusters` | Violations récurrentes groupées par type |
| `deterministic_violations_total` | Nombre total de violations de règles — objectif : 0 critical |
| `plans_with_critical_violations` | Plans ayant au moins 1 violation critique — objectif : 0 |

### `error_clusters` — décoder les clusters

Deux types de clusters :

**`"type": "deterministic"`** — violations de règles codées
```json
{
  "type": "deterministic",
  "rule": "tss_spike_week",
  "severity": "critical",
  "count": 3,
  "profile_ids": ["advanced_power_20w_15h_cyclosportive", ...],
  "sample": [{ "profile_id": "...", "week": 8, "description": "..." }]
}
```
→ Indique un bug dans le moteur à corriger dans `periodization.py` ou `plan_builder.py`.

**`"type": "llm_low_dimension"`** — score LLM systématiquement bas
```json
{
  "type": "llm_low_dimension",
  "dimension": "securite_recuperation",
  "threshold": 6.0,
  "count": 8,
  "profile_ids": [...]
}
```
→ Suggère un axe d'amélioration qualitative dans `plan_builder.py`.

### `evaluations[i]` — évaluation individuelle

```json
{
  "profile_id": "intermediate_power_12w_6h_cyclosportive",
  "plan_meta": {
    "weeks_count": 12,
    "initial_weekly_tss": 240.0,
    "peak_weekly_tss": 480.0,
    "phases": ["base", "base", "base", "build", ...],
    "recovery_weeks": [4, 8, 11]
  },
  "deterministic_checks": {
    "passed": true,
    "critical_count": 0,
    "warning_count": 1,
    "violations": [...]
  },
  "llm_evaluation": {
    "scores": {
      "coherence_physiologique": 8,
      "progressivite_charge": 7,
      "respect_profil_utilisateur": 9,
      "securite_recuperation": 6,
      "adequation_objectif": 8
    },
    "composite_score": 7.45,
    "points_forts": ["..."],
    "points_critiques": ["S3 : ..."],
    "verdict_global": "...",
    "verifier_adjustments": []
  },
  "final_score": 7.45,
  "has_critical_violations": false
}
```

**`verifier_adjustments`** : liste des corrections apportées par le Verifier au Critic.
Si vide `[]`, le Verifier a validé la critique telle quelle.

---

## 5. Ajouter des profils

### Format YAML

Ajouter un profil dans `eval/profiles/matrix.yaml` ou `edge_cases.yaml` :

```yaml
- id: mon_profil_unique          # identifiant unique snake_case
  description: "Description courte du cas testé"
  level: intermediate            # beginner | intermediate | advanced | expert
  coaching_mode: power           # power | hr
  objective_type: cyclosportive  # cyclosportive | etape_du_tour | sante | other
  target_weeks: 12               # null = pas de date cible (plan par défaut 12 sem)
  hours_per_week: 6
  preferred_days: [tuesday, thursday, saturday, sunday]
  power_meter: true
  ftp: 230                       # null si power_meter: false
  ftp_source: declared           # declared | estimated
  age: 34
  hr_max: 188
  hr_max_source: declared        # declared | estimated
  hr_rest: 58
  hr_rest_source: declared
  structured_plan_history: true
  health_constraints: false
  sex: M                         # M | F | null
  weight_kg: 72.0                # optionnel
  injury_status: null            # voir edge_cases.yaml pour exemple avec blessure
```

**Règles de cohérence à respecter :**
- Si `power_meter: false` → `ftp: null` et `coaching_mode: hr`
- Le nombre de `preferred_days` doit être compatible avec `hours_per_week`
  (3h/sem → 2 jours min, 15h/sem → 5+ jours recommandés)

---

## 6. Ajouter une règle déterministe

1. Ouvrir `eval/rules/checker.py`
2. Créer une fonction `_check_ma_regle(profile, plan) -> list[RuleViolation]` :

```python
def _check_ma_regle(profile: AthleteProfileSchema, plan: TrainingPlanSchema) -> list[RuleViolation]:
    violations = []
    for week in plan.weeks:
        if <condition>:
            violations.append(RuleViolation(
                rule_id="ma_regle",           # identifiant unique snake_case
                severity="critical",           # "critical" | "warning"
                week=week.week_number,         # None si violation plan-level
                description=f"S{week.week_number} : description précise du problème",
            ))
    return violations
```

3. Ajouter l'appel dans `check_all()` :
```python
violations.extend(_check_ma_regle(profile, plan))
```

---

## 7. Dimensions de scoring LLM

| Dimension | Poids | Bonne note (8-10) | Mauvaise note (0-5) |
|-----------|-------|-------------------|---------------------|
| `securite_recuperation` | 30% | Gap ≥ 48h entre Z4+, semaine récup dosée à 55-65% | Séances intenses consécutives, pas de récupération sur 5+ sem |
| `progressivite_charge` | 25% | Augmentation ≤ 10%/sem, récup bien dosée | Spike TSS brutal, régression de charge, stagnation |
| `respect_profil_utilisateur` | 20% | Jours respectés, volume ≤ dispo, niveau adapté | Séances hors dispo, dépassement volume, niveau mal calibré |
| `coherence_physiologique` | 15% | TSS/h adapté au niveau, zones cohérentes avec FTP/âge | Zones irréalistes, FTP incohérent avec niveau déclaré |
| `adequation_objectif` | 10% | Travail adapté à l'objectif (Z2 base, threshold, VO2) | VO2max pour objectif santé, pas de taper avant course |

**Score composite** = somme pondérée des 5 dimensions.

---

## 8. Itérer sur le moteur

Workflow recommandé :

```bash
# 1. Créer un rapport de référence (baseline)
python -m eval.runner --output eval/results/baseline.json

# 2. Modifier le moteur (plan_builder.py, periodization.py...)

# 3. Relancer l'évaluation
python -m eval.runner --output eval/results/after_fix.json

# 4. Comparer les rapports
# → Vérifier que mean_composite_score a augmenté
# → Vérifier que deterministic_violations_total a diminué
# → Vérifier que les clusters problématiques ont disparu
```

**Métriques cibles de qualité :**

| Métrique | Objectif |
|----------|----------|
| `mean_composite_score` | ≥ 7.5 |
| `critical_plans_pct` | 0% |
| `plans_with_critical_violations` | 0 |
| `deterministic_violations_total` | 0 violations critiques |
| `by_dimension.securite_recuperation.mean` | ≥ 7.0 |
