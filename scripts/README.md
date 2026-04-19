# scripts/ — Outils de développement Banister

## generate_plan.py

Génère et affiche un plan d'entraînement complet depuis un profil athlète JSON,
sans avoir besoin de lancer le bot ou de toucher la base de données.

Utile pour :
- Tester l'impact d'un changement dans le moteur (periodization, plan_builder)
- Vérifier la progression CTL/TSB projetée sur un profil donné
- Déboguer un plan généré pour un utilisateur spécifique

### Prérequis

Être dans le répertoire racine du projet avec l'environnement Python actif :

```bash
cd banister
```

---

### Usage

#### 1. Générer un profil exemple

```bash
python scripts/generate_plan.py --sample
# ou pour le sauvegarder directement :
python scripts/generate_plan.py --sample > scripts/mon_profil.json
```

#### 2. Afficher un plan

```bash
python scripts/generate_plan.py --profile scripts/sample_profile.json
```

#### 3. Choisir la date de début du plan

Par défaut la date de début est aujourd'hui. Pour simuler un plan qui démarrerait
à une autre date :

```bash
python scripts/generate_plan.py --profile scripts/sample_profile.json --start-date 2026-06-01
```

#### 4. Exporter le plan complet en JSON

```bash
python scripts/generate_plan.py --profile scripts/sample_profile.json --json plan_output.json
# Avec une date de début personnalisée :
python scripts/generate_plan.py --profile scripts/sample_profile.json --start-date 2026-06-01 --json plan_output.json
```

Le JSON produit correspond à `TrainingPlanSchema` — il peut être inséré directement
dans la colonne `plan_technical` de la table `training_plans`.

---

### Récupérer son profil depuis Supabase

```sql
SELECT profile FROM athlete_profiles WHERE user_id = <telegram_id>;
```

Copier le contenu du champ `profile` dans un fichier `.json`, puis :

```bash
python scripts/generate_plan.py --profile mon_profil.json
```

---

### Format du profil d'entrée (`AthleteProfileSchema`)

```json
{
  "objective": {
    "type": "cyclosportive",
    "target_date": "2026-09-20"
  },
  "availability": {
    "hours_per_week": 8,
    "preferred_days": ["tuesday", "thursday", "saturday", "sunday"]
  },
  "level": "intermediate",
  "structured_plan_history": true,
  "equipment": {
    "power_meter": false,
    "ftp": null,
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
  "health_constraints": false,
  "current_ctl": 52.0,
  "current_atl": 48.0,
  "current_tsb": 4.0
}
```

**Champs clés à modifier pour tester :**

| Champ | Valeurs possibles | Impact |
|-------|------------------|--------|
| `level` | `beginner` / `intermediate` / `advanced` / `expert` | Peak TSS, contenu des séances |
| `hours_per_week` | 3 – 20 | Nombre de séances, durée sortie longue |
| `coaching_mode` | `hr` / `power` | Zones utilisées, calcul TSS |
| `current_ctl` | float ou `null` | Baseline TSS, ramp rate, projection CTL |
| `target_date` | date ISO ou `null` | Durée du plan (null = 12 semaines) |
| `preferred_days` | liste de jours en anglais | Distribution des séances dans la semaine |

Si `current_ctl` est `null`, le script tourne sans la colonne CTL projeté et
utilise `hours_per_week × 40` comme TSS de départ.

---

### Lecture de la sortie

```
SEM  PHASE     TSS    CTL    TSB
  1  BASE    300.3   51.9   -0.9
  + Mar  endurance      Z2   76min   57.1 TSS  [cadence haute (95-100 rpm)]
  + Jeu  intervalles    Z3   75min   55.0 TSS  [2×20min Sweet Spot]
  ` Dim  sortie longue  Z2  172min  129.0 TSS
```

- **TSS** : charge hebdomadaire cible (somme des `tss_target` des séances)
- **CTL** : CTL théorique en fin de semaine si le plan est respecté parfaitement
- **TSB** : forme théorique en fin de semaine (`CTL - ATL`)
- **RECUP** : semaine de récupération (TSS × 0.60, Z1/Z2 uniquement)
- `[...]` : détail de la structure d'intervalles ou variante d'endurance

**Interprétation TSB fin de plan :**

| TSB | Signification |
|-----|--------------|
| < −30 | Surmenage — taper trop court ou charge trop élevée |
| −30 à 0 | Fatigue normale — encore en phase de charge |
| 0 à +5 | Bonne forme |
| **+5 à +15** | **Forme de pointe — zone cible pour le jour de course** |
| +15 à +20 | Très frais — taper peut-être trop long |
| > +20 | Transition — risque de désentraînement |
