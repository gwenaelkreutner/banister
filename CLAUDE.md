# Banister — Guide Claude

## Vue d'ensemble

Bot Telegram de coaching cyclisme IA. Génère et adapte des plans d'entraînement personnalisés.
Architecture en un seul process : aiogram v3 (bot) + FastAPI (webhooks/OAuth) + moteur déterministe Python + LLM.

**Principe fondateur : le LLM ne calcule jamais la charge d'entraînement — moteur déterministe uniquement.**

## ⚠️ Refonte open source en cours

Ce document décrit **l'état actuel du code**, pas la cible. Une refonte vers un produit self-hosted open
source est spécifiée dans `specs/001` à `007`, et gouvernée par `.specify/memory/constitution.md`.

Ce qui va changer, et qui rendra des sections entières de ce fichier obsolètes :

| Décision | Effet sur ce document |
|---|---|
| intervals.icu **remplace** Strava (source unique et obligatoire) | toute la section Pipeline Strava |
| SQLite local remplace Supabase | Tables + migrations + `client.py` |
| Le log manuel de séance **disparaît** | `/log`, `SessionLogStates`, `tss_from_rpe()` |
| TSS/zones consommés depuis la source, plus recalculés | `tss.py`, `zones.py` supprimés |
| `SessionSpec` gagne des étapes structurées | Schémas Pydantic + génération de plan |

**Ordre de construction** (les numéros de spec sont des identifiants, pas une séquence) :
`003` base locale → `002` intervals.icu → `004` séances structurées → `005` push calendrier →
`006` guardrails → `007` premier lancement.

Mettre ce fichier à jour **au fil de** chaque migration, pas après coup.

## Commandes essentielles

```bash
# Démarrer (polling + FastAPI ensemble)
python -m uvicorn app.main:app --port 8000 --reload

# Tests
pytest tests/
pytest tests/test_strava/          # tests pipeline Strava
pytest tests/test_engine/          # tests moteur

# Lint
ruff check app/ tests/

# Tunnel pour OAuth Strava en local
ngrok http 8000

# Simuler une activité Strava (PowerShell)
./simulate_ride.ps1
```

## Stack

- **Runtime** : Python 3.13
- **Bot** : aiogram v3 (async FSM, MemoryStorage)
- **API** : FastAPI — sert webhooks Telegram + OAuth/webhook Strava
- **DB** : Supabase (PostgreSQL) via SQLAlchemy asyncpg — SSL obligatoire (`?ssl=require`)
- **LLM** : OpenRouter (provider actuel) ou Anthropic — abstraction multi-provider dans `app/llm/providers/`
  - Modèle test : `arcee-ai/trinity-large-preview:free`
  - Configurable via `LLM_MODEL` / `CHAT_MODEL` dans `.env`
- **Strava** : OAuth2 + webhooks + import historique

## Architecture

```
app/
├── main.py                  # FastAPI + lifespan (polling dev / webhook prod)
├── config.py                # Settings Pydantic
├── core/
│   ├── persona.py           # load_persona() → Persona depuis personas/*.yaml
│   │                        # ⚠️ construit mais JAMAIS appelé — prompts.py a encore ses prompts en dur
│   ├── exceptions.py        # BanisterError + PersonaNotFoundError
│   └── types.py
├── bot/
│   ├── setup.py             # Dispatcher + middlewares + routers (ordre critique)
│   ├── states.py            # FSM : SetupStates, PlanStates, SessionLogStates
│   ├── middlewares/
│   │   ├── db_session.py    # Ouvre AsyncSession (doit précéder single_user)
│   │   └── single_user.py   # Garde TELEGRAM_OWNER_ID + injection User (pas d'upsert)
│   ├── keyboards/           # Prefixes callbacks : setup: / plan: / log: / strava: / chat: / rem:
│   ├── routers/             # Ordre réel dans setup.py : common → setup → plan → strava
│   │                        # → session_log → forme → recap → reminders → chat (DERNIER)
│   └── (chat doit rester en dernier — catch-all)
├── db/
│   ├── client.py            # AsyncSessionFactory (expire_on_commit=False)
│   ├── models/              # ORM SQLAlchemy
│   └── repositories/        # Accès DB — jamais de SQL dans les handlers
│       └── weekly_adherence_repo.py  # upsert + get_recent — persistance taux d'adhérence /recap
├── services/                # Orchestration : repos + LLM, sans dépendance aiogram
│   └── weekly_recap.py      # compute_weekly_recap() → WeeklyRecapResult
├── engine/                  # Moteur déterministe — zéro LLM ici
│   ├── schemas.py           # Pydantic : AthleteProfileSchema, TrainingPlanSchema, SessionSpec
│   ├── zones.py             # Zones puissance/FC depuis FTP/hr_max
│   ├── tss.py               # Calcul TSS (power/hr/rpe)
│   ├── periodization.py     # Blocs Base/Build/Peak/Taper
│   ├── plan_builder.py      # Génération plan complet
│   ├── plan_modifier.py     # Modification plan (outil LLM)
│   ├── atl_ctl.py           # ATL/CTL/TSB (EMA τ=7j/42j) + tss_from_rpe()
│   ├── adherence_kpi.py     # Score KPI par séance (0–2.0 pts) + bloc KPI hebdo
│   └── weekly_snapshot.py   # WeeklySnapshot : tendance charge, monotonie Foster
├── llm/
│   ├── factory.py           # Sélection provider (openrouter | anthropic)
│   ├── providers/           # anthropic.py, openrouter.py — interface commune generate()
│   ├── chat_client.py       # run_agentic_loop() — max 2 itérations outils
│   ├── chat.py              # run_chat() → (text, intent, tool_used, pending_proposal)
│   ├── tools.py             # 4 outils LLM + build_system_prompt()
│   ├── prompts.py           # Contexte système (profil, plan, métriques)
│   ├── activity_analysis.py # Feedback post-séance enrichi (5 blocs, tone TSB)
│   └── narrator.py          # Résumé narratif semaine (texte pur)
└── strava/
    ├── oauth.py             # build_auth_url() + HMAC state
    ├── client.py            # get_athlete(), get_athlete_stats(), get_activities()
    ├── history.py           # import_history() : 3 appels API + TSS + _analyze()
    ├── analysis_models.py   # DTOs : RawActivity, RawActivityStreams, AnalyzedSession
    ├── fetcher.py           # StravaActivityFetcher — ingestion sans logique métier
    ├── analyzer.py          # SessionAnalyzer — RawActivity → AnalyzedSession
    ├── matching.py          # Matching activité ↔ séance planifiée (score 0-100)
    └── webhook.py           # Réception événements activité Strava
```

## Pipeline Strava

Trois couches séparées pour décorréler l'API Strava des calculs métier :

```
StravaActivityFetcher.fetch_raw_activity()
    → Tier 1 : pas de streams (activité manuelle ou < 20min)
    → Tier 2 : streams légers [time, watts, heartrate] (ride/run > 20min avec HR ou power)
    → Tier 3 : streams complets [+velocity, grade, moving, altitude] (séance planifiée ou course/test)
    → RawActivity

SessionAnalyzer.analyze(raw_activity, ftp=..., hr_max=..., ...)
    → Calcule NP (strava weighted_avg > NP calculé streams)
    → Calcule time_in_zones_s (power si FTP dispo, sinon HR)
    → Calcule TSS, IF, session_type_real, variability_index (NP/avg_power)
    → Métriques qualité : respect_zones_score, cardiac_drift_index, intervals_consistency_index
    → Déduit environment ("indoor" si VirtualRide, sinon "outdoor")
    → AnalyzedSession

# Si séance alignée avec le plan → upgrade Tier 3 + ré-analyse
fetcher.ensure_tier3_streams() → analyzer.analyze() (2e passe, planned_zone injecté)
```

**Priorité NP** : `weighted_average_watts` Strava > calculé depuis streams > `None` (jamais estimé).

**Détection `session_type_real`** (dans `SessionAnalyzer._detect_session_type`) :
- `"intervals"` : z4+ ≥ 10% + variabilité watts élevée (P95/P05 ratio > 0.6)
- `"tempo"` : z3+z4 ≥ 60% (sorties > 3h) ou ≥ 40% (< 3h)
- `"long_ride"` : z1+z2 ≥ 60% + durée ≥ 2h30, ou durée ≥ 2h30 sans zone dominante
- `"endurance"` : z1+z2 ≥ 60% + 1h–2h30
- `"recovery"` : z1+z2 ≥ 60% + < 1h
- `"unknown"` : Tier 1 (pas de streams) ou activité manuelle

## Matching activité ↔ plan

**Principe** : le matching est sémantique — l'activité est comparée à toutes les séances de sa semaine d'entraînement et on prend celle qui lui ressemble le plus, pas forcément la plus proche temporellement.

**`evaluate_activity_plan_match(plan, analyzed, activity_date, used_slots)`** :
- Fenêtre : ±2 jours, bornée à la même semaine d'entraînement (relative à `plan.start_date`)
- `used_slots: frozenset[(week_number, day_of_week)]` — slots déjà pris par d'autres activités (anti-double-candidature)
- Retourne `ActivitySessionMatch(candidate, score, all_slots_taken)`

**Score sur 100 pts** (`score_activity_vs_session(analyzed, session_spec)`) :
| Dimension | Pts | Condition |
|---|---|---|
| Durée | 30 | ratio elapsed/planned proche de 1 |
| TSS/charge | 30 | ratio tss/tss_target proche de 1 |
| Type séance | 25 | matrice `_TYPE_COMPAT[session_type_real][workout_type]` |
| Zone dominante | 5 | bonus si `dominant_zone == zone_code` |
| Indoor/outdoor | 10 | cohérence environnement + type séance |

Si `session_type_real == "unknown"` (Tier 1) → type score neutre à 12 pts, pas de pénalité.

**`all_slots_taken`** : `True` si des candidats existent dans la fenêtre mais tous déjà pris → webhook affiche message "Sortie bonus" au lieu de "hors plan".

**`build_activity_session_pairs(plan_schema, plan_start_date, session_logs, week_number)`** :
- Construit les paires `SessionPair(planned_date, day_of_week, session_spec, session_log)` pour une semaine
- Inclut : ✅ réalisées, ❌ manquées (passées), 📅 à venir, 🔄 bonus non planifiées
- Les bonus dont la `logged_date` correspond à la date réelle d'une séance matchée sont supprimés (évite les doublons le même jour réel)
- Utilisé par `build_system_prompt()` pour construire le contexte LLM semaine en cours

**Webhook — 3 cas de message hors-plan** :
1. `candidate is None, all_slots_taken=False` → "Aucune séance planifiée à ±2 jours"
2. `candidate is None, all_slots_taken=True` → 🔄 "Sortie bonus enregistrée"
3. `candidate found, score < 50` → détail du score avec raisons

## Tables Supabase

| Table | Description |
|-------|-------------|
| `users` | Compte Telegram, flags onboarding, préférences rappels (`reminders_enabled`, `reminder_hour`, `reminder_minute`, `reminder_last_sent_at`) |
| `athlete_profiles` | `profile` JSONB → `AthleteProfileSchema` |
| `training_plans` | `plan_technical` JSONB → `TrainingPlanSchema`, `start_date`, `is_active` |
| `oauth_connections` | Tokens Strava (access/refresh, expires_at, provider_user_id) |
| `session_logs` | `plan_id` NOT NULL, `tss_actual`, `rpe_emoji`, `logged_date` ; métriques qualité (`cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`, `session_type_real`, `variability_index`, `intensity_factor`, `dominant_zone`) ; contexte Strava (`elevation_gain_m`, `average_temp_c`, `athlete_count`) |
| `chat_messages` | Historique LLM (role, content, intent, tool_used) |
| `activities` | Import historique Strava (`tss`, `tss_method`, `device_watts`) |
| `weekly_adherence` | Taux d'adhérence hebdomadaire — upsert à chaque `/recap` ; clé `(user_id, week_start_date)` ; colonnes : `sessions_done`, `sessions_planned`, `compliance_pct`, `tss_7d`, `week_number`, `plan_id` |

Migrations : un seul fichier `migrations/init.sql` (schéma complet), joué automatiquement par Docker au
premier démarrage. Les anciennes migrations numérotées `001`→`013` ont été fondues dedans.

## Schémas Pydantic clés

**`SessionSpec`** (séance dans le plan) :
```python
day_of_week: int          # 0=Lundi, 6=Dimanche
workout_type: str         # "long_ride" | "intervals" | "endurance" | "recovery"
zone_code: str            # "Z2", "Z4"...
duration_minutes: int
target_time_in_zone_minutes: int  # temps cible dans zone principale
tss_target: float
```

**Convertir avant `build_system_prompt()`** :
```python
TrainingPlanSchema.model_validate(plan.plan_technical)  # pas l'ORM directement
```

## Règles importantes

### SQLAlchemy JSONB
Mutations non auto-détectées → obligatoire :
```python
plan.plan_technical = new_data
flag_modified(plan, "plan_technical")
await session.flush()
```

### Telegram / parse_mode
- `parse_mode="HTML"` partout où les messages contiennent `/connect_strava` ou underscores
- `parse_mode="Markdown"` uniquement si le texte est garanti sans underscore hors italique

### FSM aiogram
- `MemoryStorage` → états perdus au redémarrage — `UserLoaderMiddleware` restaure `PlanStates.ACTIVE` depuis la DB
- `FSMContext` inaccessible dans les middlewares (injecté après par aiogram)
- `chat_router` doit être enregistré en DERNIER dans `setup.py` (catch-all)

### Strava OAuth
- State format : `{telegram_id}:{context}.{hmac_hex}` — `context` = `"main"` | `"onboarding"`
- `verify_state()` → `tuple[int, str]` ou `None`

### Calcul TSS / HRSS

**Priorité dans `SessionAnalyzer.analyze()`** : power (NP/FTP) > **HRSS streams** > fallback scalaire

**HRSS — TRIMP de Banister** (`calc_hrss()` dans `app/engine/tss.py`) :
```
HRR_i  = (HR_i − HR_rest) / (HR_max − HR_rest)   # réserve cardiaque, [0, 1]
stress_i = dt_i × HRR_i × 0.64 × exp(k × HRR_i)  # k=1.92 H / k=1.67 F
TRIMP    = Σ stress_i
HRSS     = TRIMP / TRIMP_1h_LTHR × 100            # 100 = 1h exactement au seuil
```
- Calcul continu sur la série temporelle secondaire (streams Strava, 1 pt/s)
- NumPy vectorisé — performant même sur sorties 6h+ (≥ 21 600 pts)
- `calc_hrss()` renvoie `HRSSResult(hrss, trimp, tss_method="hrss", rpe_factor, fatigue_anomaly)`

**Pondération RPE** (paramètre optionnel `user_rpe` 1-10) :
- HRR moyen pondéré par le temps → RPE cardiaque estimé = `hrr_moy × 10`
- Si `rpe_déclaré − rpe_cardiaque ≥ 3` : multiplicateur 1.10–1.20 + `FatigueAnomaly`
- `FatigueAnomaly` sérialisée en `dict` dans `AnalyzedSession.fatigue_anomaly`

**`calc_tss()` — fallback scalaire** (inchangé) :
- Utilisé par `history.py` (import historique, pas de streams) et si pas de streams HR
- Retourne `(tss, méthode)` — méthode ∈ `{"power", "hr", "estimation"}`

**Paramètres `analyze()`** :
- `sex: str | None` — `"M"` | `"F"` (défaut `"M"` si absent)
- `user_rpe: int | None` — smiley converti en entier 1-10
- `ftp_source` / `hr_max_source` : `"declared"` | `"estimated"` (jamais `"strava"`)

### ATL/CTL/TSB
- `compute_fitness_from_any()` accepte liste mixte `SessionLog` + `Activity` (duck-typing)
- Si historique < 84j → amorcer CTL avec `estimate_initial_ctl(weekly_tss)` + `seed_date`
- Le déclin final jusqu'à `date.today()` est appliqué automatiquement

**Projection CTL théorique** (`project_fitness_from_plan()`) :
- Simule CTL/ATL/TSB en suivant le plan parfaitement (`tss_target` au lieu du TSS réel)
- Même formule EMA — retourne `list[FitnessProjectionPoint]` (un point par séance planifiée)
- `FitnessProjectionPoint` : `date, ctl, atl, tsb, week_number, tss_planned`
- Sert de référence pour mesurer la déviation du suivi réel (futur KPI d'adhérence)

### Snapshot hebdomadaire (`app/engine/weekly_snapshot.py`)
- `compute_weekly_snapshot(logs, today)` → `WeeklySnapshot` (tss_7d, tss_6w_avg, load_trend_pct, sessions_done_7d, monotony_index)
- Fenêtre 7j : `[today-6 .. today]` ; 6 semaines : les 6 semaines complètes avant la fenêtre courante (pas de chevauchement)
- **Monotonie Foster** : `mean(TSS journaliers) / std` — score > 2.0 = charge monotone = risque ; `None` si std=0 ou < 2 jours
- Fonction pure, pas de requête DB — prend la liste `logs` déjà en mémoire

### Analyse LLM post-séance (`app/llm/activity_analysis.py`)
- `generate_activity_analysis(**kwargs)` — prompt structuré en 5 blocs : Séance / Puissance / Qualité / Contexte / Forme & Charge
- 3 paramètres optionnels Variable Reward : `highlight_category`, `personal_record`, `storytelling_mode`
- `storytelling_mode` ∈ `"journalist"` | `"analyst"` | `"coach"` → sélectionne un arc narratif en 3 phrases exactes via `build_narrative_system_prompt()` (dans `prompts.py`)
- Si `storytelling_mode=None` → comportement classique 4-5 phrases + `build_ux_system_prompt()`
- `intensity_factor` stocké en DB au moment du webhook (FTP de l'époque, immuable)
- `variability_index` non transmis au LLM si `duration_minutes < 30` (non représentatif sur courtes sorties)
- TSB-driven tone : `tsb < -30` → protecteur | `tsb >= +5` → motivant | sinon → équilibré (seuils alignés sur `tsb_label()`)

### Rappels de séance (`app/bot/routers/reminders.py` + `app/main.py`)

**Commande `/reminders`** — menu inline état-dépendant :
- Rappels **activés** : bouton "Désactiver" + grille des 5 créneaux horaires (heure active cochée `✓`)
- Rappels **désactivés** : bouton "Activer" uniquement, sans grille
- Callbacks : `rem:toggle` (bascule on/off) | `rem:time:<H>:<M>` (change l'heure)
- Menu se met à jour inline (`edit_text`) — pas de nouveau message à chaque clic

**Scheduler** (`_session_reminder_scheduler` dans `main.py`) :
- Tourne toutes les 60 secondes via `asyncio.sleep(60)` dans `lifespan`
- À chaque tick : `now_cet = utcnow + 1h` → `get_users_to_remind(hour, minute)`
- Filtre : `reminders_enabled=True AND reminder_hour=H AND reminder_minute=M AND reminder_last_sent_at < today`
- Pour chaque user : charge le plan actif → cherche `SessionSpec.day_of_week == today.weekday()`
- Si séance trouvée → envoie le message ; jour de repos = silence
- Dans tous les cas : `reminder_last_sent_at = today` (idempotence — 1 seul envoi/jour)

**Format du message de rappel** (HTML, zéro LLM) :
```
🚴 Séance du jour — Semaine N/total

<b>Type</b> Zx — durée min
Objectif : X min en Zx

📅 /plan pour voir le programme complet
💬 Une question sur ta séance ? Pose-la ici !
```

**Colonnes `users`** (migration `011`) :
```
reminders_enabled    BOOLEAN  DEFAULT TRUE
reminder_hour        SMALLINT DEFAULT 7      ← UTC+1 (CET)
reminder_minute      SMALLINT DEFAULT 30
reminder_last_sent_at DATE    NULL
```

**Défaut** : activé à 7h30 CET dès la fin de l'onboarding (colonnes initialisées avec `server_default`).

### Variable Reward — notification post-ride (`app/strava/highlight.py`)
- `select_highlight(analyzed, fitness, weekly_snap)` → `HighlightResult` — tirage pondéré parmi 6 catégories
- `detect_personal_records(all_logs, analyzed, current_log_id)` → `PersonalRecord | None` — in-memory, pas de requête DB
- Notification en 3 messages : A (teaser, silencieux) → B (métrique héros, silencieux) → C (verdict + clavier RPE, seule vibration)
- Post-RPE : `edit_text` sur Message C → "Ton coach analyse..." → edit → récit LLM 3 phrases (aucune notification supplémentaire)

## Flux de configuration (`/setup`)

```
/setup → SPORT → GOAL → DATE → VOLUME → POWER → AGE → _finalize_setup() → plan
```

6 étapes, `SetupStates` dans `app/bot/states.py`, tout dans `app/bot/routers/setup.py` (~456 lignes).
Relancer `/setup` régénère le plan intégralement.

`_build_profile()` assemble l'`AthleteProfileSchema` ; si Strava est connecté, la forme actuelle
(`current_ctl` / `current_atl` / `current_tsb`) est injectée depuis l'historique importé.

## Variables d'environnement

```
DATABASE_URL=postgresql+asyncpg://postgres:<pwd>@<host>/postgres?ssl=require
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_URL=https://<domaine>/webhook/telegram   # prod uniquement
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_REDIRECT_URI=https://<domaine>/auth/strava/callback
STRAVA_STATE_SECRET=<secret>
STRAVA_WEBHOOK_VERIFY_TOKEN=<secret>
LLM_PROVIDER=openrouter          # ou "anthropic"
LLM_MODEL=arcee-ai/trinity-large-preview:free
CHAT_MODEL=arcee-ai/trinity-large-preview:free
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...            # si LLM_PROVIDER=anthropic
```

## Navigation rapide

| Tâche | Fichier |
|-------|---------|
| Ajouter commande bot | `app/bot/routers/` + enregistrer dans `setup.py` avant `chat_router` |
| Modifier calcul TSS | `app/engine/tss.py` |
| Modifier génération plan | `app/engine/plan_builder.py` |
| Modifier périodisation | `app/engine/periodization.py` |
| Modifier ATL/CTL/TSB | `app/engine/atl_ctl.py` |
| Projection CTL théorique (suivi plan) | `app/engine/atl_ctl.py` — `project_fitness_from_plan()` |
| Modifier snapshot hebdo (monotonie, tendance) | `app/engine/weekly_snapshot.py` |
| Modifier récap hebdo (logique + LLM) | `app/services/weekly_recap.py` |
| Lire/écrire l'adhérence hebdomadaire | `app/db/repositories/weekly_adherence_repo.py` |
| Modifier le scheduler dimanche 20h | `app/main.py` — `_weekly_recap_scheduler()` |
| Modifier les rappels de séance (contenu message) | `app/main.py` — `_format_reminder()` |
| Modifier les rappels de séance (menu /reminders) | `app/bot/routers/reminders.py` |
| Ajouter un créneau horaire aux rappels | `app/bot/routers/reminders.py` — `_TIME_SLOTS` |
| Modifier feedback LLM post-séance | `app/llm/activity_analysis.py` |
| Modifier la notification post-ride (3 messages stagés) | `app/strava/webhook.py` — `_notify_staged_rpe_request()` |
| Modifier notification "sortie bonus" (slot déjà pris) | `app/strava/webhook.py` — `_notify_bonus_activity()` |
| Modifier notification "activité hors plan" | `app/strava/webhook.py` — `_notify_unplanned()` |
| Modifier le Variable Reward (catégories highlight) | `app/strava/highlight.py` — `select_highlight()` |
| Modifier les modes narratifs LLM (journalist/analyst/coach) | `app/llm/prompts.py` — `build_narrative_system_prompt()` |
| Modifier le reveal post-RPE (edit-then-reveal) | `app/bot/routers/session_log.py` — `_reveal_activity_analysis()` |
| Modifier analyse activité (zones, NP, IF, session_type_real) | `app/strava/analyzer.py` — `SessionAnalyzer` |
| Modifier scoring matching activité ↔ plan | `app/strava/matching.py` — `score_activity_vs_session()` |
| Modifier fenêtre de matching / candidats | `app/strava/matching.py` — `find_plan_candidate()` |
| Modifier vue plan+réalisé pour le LLM (system prompt) | `app/llm/tools.py` — `build_activity_session_pairs()` + `_format_week_pairs()` |
| Ajouter outil LLM | `app/llm/tools.py` (définition JSON Schema) + `_execute_tool()` dans `app/llm/chat.py` |
| Ajouter champ DB | `app/db/models/` + `app/db/repositories/` + `migrations/00N_...sql` |
| Architecture complète | `docs/ARCHITECTURE.md` |
