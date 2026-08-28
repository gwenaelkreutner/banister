# Banister — Guide Claude

## Vue d'ensemble

Bot Telegram de coaching cyclisme IA. Génère et adapte des plans d'entraînement personnalisés.
Architecture en un seul process : aiogram v3 (bot) + FastAPI (webhooks/OAuth) + moteur déterministe Python + LLM.

**Principe fondateur : le LLM ne calcule jamais la charge d'entraînement — moteur déterministe uniquement.**

## ⚠️ Refonte open source en cours

Ce document décrit **l'état actuel du code**, pas la cible. Une refonte vers un produit self-hosted open
source est spécifiée dans `specs/001` à `007`, et gouvernée par `.specify/memory/constitution.md`.

**✅ Fait** : spec 003 (SQLite local remplace Supabase), spec 002 (intervals.icu remplace Strava, log manuel
supprimé), spec 004 (séances structurées, bibliothèque de templates, fitting), spec 005 (push des séances
vers le calendrier intervals.icu — `/publish`, `/unpublish`) — voir sections ci-dessous, à jour.

Ce qui reste à faire, et qui rendra d'autres sections de ce fichier obsolètes :

| Décision | Effet sur ce document |
|---|---|
| Câblage de `load_persona()` (voix du coach configurable) | Sections chat/prompts |

**Ordre de construction** (les numéros de spec sont des identifiants, pas une séquence) :
~~`003` base locale~~ (fait) → ~~`002` intervals.icu~~ (fait) → ~~`004` séances structurées~~ (fait) →
~~`005` push calendrier~~ (fait) → `006` guardrails → `007` premier lancement.

Mettre ce fichier à jour **au fil de** chaque migration, pas après coup.

## Commandes essentielles

```bash
# Démarrer (polling + FastAPI ensemble)
python -m uvicorn app.main:app --port 8000 --reload

# Tests
pytest tests/
pytest tests/test_providers/       # tests intervals.icu (client, mapper, poller, notifier)
pytest tests/test_analysis/        # tests matching activité ↔ plan
pytest tests/test_engine/          # tests moteur

# Lint
ruff check app/ tests/
```

## Stack

- **Runtime** : Python 3.13
- **Bot** : aiogram v3 (async FSM, MemoryStorage)
- **API** : FastAPI — sert le webhook Telegram uniquement (plus d'endpoint entrant côté source de données ;
  voir Pipeline intervals.icu ci-dessous)
- **DB** : SQLite local (fichier unique sous `DATA_DIR`, `data/` par défaut) via SQLAlchemy + `aiosqlite`.
  Aucun service séparé à administrer — le fichier est créé et migré automatiquement au démarrage.
- **LLM** : OpenRouter (provider actuel) ou Anthropic — abstraction multi-provider dans `app/llm/providers/`
  - Configurable via `LLM_MODEL` / `CHAT_MODEL` dans `.env`
  - ⚠️ Certains modèles OpenRouter consomment beaucoup de tokens de "raisonnement" cachés avant d'émettre
    du contenu visible — un `max_tokens` trop bas se traduit par `finish_reason=length` et un contenu
    `null`, silencieusement absorbé par le fallback déterministe. Vu en conditions réelles avec
    `stepfun/step-3.5-flash` : 300–2048 insuffisant, 4000 suffisant.
- **intervals.icu** : source unique et obligatoire des activités — clé API personnelle (basic auth),
  interrogation périodique (pas de webhook entrant, décision verrouillée en spec 001/002)

## Architecture

```
app/
├── main.py                  # FastAPI + lifespan (polling dev / webhook prod)
├── config.py                # Settings Pydantic
├── core/
│   ├── persona.py           # load_persona() → Persona depuis personas/*.yaml
│   │                        # ⚠️ construit mais JAMAIS appelé — prompts.py a encore ses prompts en dur
│   └── exceptions.py        # BanisterError + PersonaNotFoundError
├── bot/
│   ├── setup.py             # Dispatcher + middlewares + routers (ordre critique)
│   ├── states.py            # FSM : SetupStates, PlanStates (SessionLogStates supprimé — spec 002)
│   ├── middlewares/
│   │   ├── db_session.py    # Ouvre AsyncSession (doit précéder single_user)
│   │   └── single_user.py   # Garde TELEGRAM_OWNER_ID + injection User (pas d'upsert)
│   ├── keyboards/           # Prefixes callbacks : setup: / plan: / log: / chat: / rem: / pub:
│   ├── routers/             # Ordre réel dans setup.py : common → setup → plan
│   │                        # → session_log → forme → recap → reminders → publish → chat (DERNIER)
│   │   └── publish.py       # /publish (approbation + écriture calendrier), /unpublish (spec 005)
│   └── (chat doit rester en dernier — catch-all)
├── db/
│   ├── client.py            # AsyncSessionFactory (expire_on_commit=False)
│   ├── models/              # ORM SQLAlchemy
│   └── repositories/        # Accès DB — jamais de SQL dans les handlers
│       ├── weekly_adherence_repo.py  # upsert + get_recent — persistance taux d'adhérence /recap
│       └── publication_repo.py       # PublicationApproval + PublishedEntry (spec 005)
├── services/                # Orchestration : repos + LLM, sans dépendance aiogram
│   ├── weekly_recap.py      # compute_weekly_recap() → WeeklyRecapResult
│   ├── activity_feedback.py # assemble_activity_feedback() — contexte post-séance, sans dépendance bot
│   ├── fitness.py           # get_current_fitness() — CTL/ATL/TSB courants depuis la table wellness
│   └── publication.py       # spec 005 : cycle de vie approbation, barrière authorize_publication(),
│                            # diff plan↔calendrier, check_divergence(), retrait — sans dépendance aiogram
├── engine/                  # Moteur déterministe — zéro LLM ici
│   ├── schemas.py           # Pydantic : AthleteProfileSchema, TrainingPlanSchema, SessionSpec, Step, RepeatGroup
│   ├── periodization.py     # Blocs Base/Build/Peak/Taper
│   ├── plan_builder.py      # Génération plan complet — sélectionne depuis session_library.py
│   ├── plan_modifier.py     # Modification plan (outil LLM) — préserve/adapte les steps
│   ├── session_library.py   # Charge/valide sessions/*.yaml, sélection déterministe (spec 004)
│   ├── fitting.py           # Adapte un template à une cible de charge (spec 004, pas encore branché à generate_plan())
│   ├── session_render.py    # Description dérivée des steps, paramétrée par langue (spec 004)
│   ├── atl_ctl.py           # ATL/CTL/TSB (EMA τ=7j/42j) — calculé localement, la source ne le fournit pas
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
└── providers/
    ├── intervals/           # Source de données unique — intervals.icu
    │   ├── client.py        # Auth basic (clé API) ; lecture (get/list) + écriture calendrier
    │   │                    # (_post/_put/_delete, list/create/update/delete_event — spec 005)
    │   ├── errors.py        # Erreurs typées (clé invalide, indisponibilité, quota)
    │   ├── mapper.py        # Payload intervals.icu (183 champs) → AnalyzedSession
    │   ├── history.py       # Import historique à la première connexion (idempotent)
    │   ├── poller.py        # Interrogation périodique — détecte les nouvelles activités
    │   ├── notifier.py      # Notification étagée post-sortie + clavier RPE
    │   ├── wellness.py      # HRV / FC repos / sommeil (spec 006 guardrails consomme, pas encore branché)
    │   ├── workout_dsl.py   # spec 005 : Step/RepeatGroup → texte DSL intervals.icu + hash_session_content()
    │   └── calendar.py      # spec 005 : publish_sessions() (diff idempotent), withdraw_event(),
    │                        # remote_event_hash() ; ne touche jamais la DB (couche provider pure)
    └── analysis/            # Logique métier que la source ne fournit pas
        ├── analysis_models.py  # AnalyzedSession (DTO commun, provider-agnostic)
        ├── matching.py          # Matching activité ↔ séance planifiée (score 0-100)
        └── highlight.py         # Variable Reward — sélection fait marquant + record personnel
```

## Pipeline intervals.icu

Interrogation périodique uniquement — aucun endpoint entrant exposé (décision verrouillée en spec 001 :
les webhooks intervals.icu exigent d'enregistrer une application et ajoutent leur propre délai de
consolidation).

```
poller.py (tick périodique, cf. INTERVALS_POLL_INTERVAL_MINUTES, défaut 5 min)
    → client.list_activities() : liste des activités récentes
    → détecte les nouvelles (sync_state), une notification par activité, jamais de doublon ni d'oubli
    → mapper.map_activity(payload) → AnalyzedSession

mapper.map_activity()
    → CTL/ATL/TSB, TSS, zones, FTP/LTHR : consommés tels quels depuis la source, jamais recalculés
    → decoupling (intervals.icu, en %) → cardiac_drift_index (fraction signée, ÷100 — unités différentes,
      bug réel trouvé en conditions réelles : notification affichant "+1571%" avant la conversion)
    → respect_zones_score, session_type_real, intervals_consistency_index : la source ne connaît pas le
      plan, donc conservés en calcul local (voir app/providers/analysis/)
    → AnalyzedSession

# Si la source est injoignable : l'ancienneté des données est annoncée, jamais substituée par une
# estimation présentée comme actuelle (FR-020 / Constitution Principe IV)
```

**Import d'historique** (`history.py`) : à la première connexion, un seul appel `list_activities()`
ramène tout l'historique disponible (jusqu'à ~120 jours en un seul call, pas de pagination observée) —
`activity_repo.bulk_insert()` avec `on_conflict_do_nothing` le rend idempotent, donc réentrant sans
risque après une interruption.

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

Si `session_type_real == "unknown"` → type score neutre à 12 pts, pas de pénalité.

**`all_slots_taken`** : `True` si des candidats existent dans la fenêtre mais tous déjà pris → le poller
affiche message "Sortie bonus" au lieu de "hors plan" (`app/providers/intervals/notifier.py`).

**`build_activity_session_pairs(plan_schema, plan_start_date, session_logs, week_number)`** :
- Construit les paires `SessionPair(planned_date, day_of_week, session_spec, session_log)` pour une semaine
- Inclut : ✅ réalisées, ❌ manquées (passées), 📅 à venir, 🔄 bonus non planifiées
- Les bonus dont la `logged_date` correspond à la date réelle d'une séance matchée sont supprimés (évite les doublons le même jour réel)
- Utilisé par `build_system_prompt()` pour construire le contexte LLM semaine en cours

**Notification — 3 cas de message hors-plan** (`app/providers/intervals/notifier.py`) :
1. `candidate is None, all_slots_taken=False` → "Aucune séance planifiée à ±2 jours"
2. `candidate is None, all_slots_taken=True` → 🔄 "Sortie bonus enregistrée"
3. `candidate found, score < 50` → détail du score avec raisons

## Publication vers le calendrier intervals.icu (spec 005)

**Première mutation sortante du projet.** Tout ce que l'athlète possède était en lecture seule jusqu'ici.

**Principe** : rien n'est écrit sans un `PublicationApproval` `approved` dont le `content_hash` correspond
**exactement** à ce que l'athlète a vu. `services/publication.py::authorize_publication()` est la barrière
unique — appelée en première instruction de `execute_publication()`, donc tout chemin d'écriture est gardé
par construction (vérifié par grep, pas au feeling — quickstart Scénario 2).

**Flux** :
```
/publish → request_publication() : calcule l'horizon (semaine courante + suivante, FR-010),
           liste chaque séance + date + caveat « active le transfert vers ta montre » (1ère fois),
           enregistre un PublicationApproval pending lié au content_hash du plan
  ↓ [✅ Publier]
authorize_publication() → refuse si hash ≠ (plan changé → StaleApprovalError, FR-004)
  ↓
calendar.publish_sessions() : diff idempotent (l'API n'a PAS d'upsert — research R2) :
    list_events() la fenêtre → garde uniquement external_id `banister:` (FR-015)
    hash connu == hash courant + événement présent → "unchanged" (rend la reprise possible, FR-016)
    présent, contenu différent → update_event()   |   absent → create_event()
    séance sans steps → refusée (FR-009)  |  push_errors sur l'événement écrit → refusée + delete
  ↓
US5 : événement modifié/supprimé à la main par l'athlète → "conflict", jamais écrasé/recréé (FR-023/024)
      catégorie ≠ WORKOUT (activité réalisée) → jamais touchée (FR-025)
US4 : séance retirée du plan → withdraw_event() + mark_withdrawn (FR-019)
      séances passées (`session_date < today`) exclues de TOUS les chemins (FR-011/022)
```

**`/unpublish`** → `withdraw_all_publications()` : n'itère que nos propres lignes `PublishedEntry`
→ « 100 % des nôtres, 0 % du reste » vrai par construction (SC-006).

**Divergence** (`check_divergence()` / `describe_divergence_for_coach()`) : dérivée à la demande, jamais
stockée. Si le plan a évolué depuis la publication, le contexte LLM (`build_system_prompt(calendar_divergence=…)`,
câblé dans `chat.py`) le dit au coach au lieu de laisser croire que le calendrier est à jour (FR-020).

**Hash de contenu** : `workout_dsl.hash_session_content(date, name, rendered_dsl)` — SHA-256, partagé
entre la couche provider (I/O) et l'orchestration. Exclut l'id serveur et la charge/durée (dérivées par
intervals.icu du DSL — R4). Vérifié en conditions réelles : intervals.icu renvoie `description` au byte près.

**Layering** : `calendar.py` = I/O calendrier pur, **ne touche jamais la DB** ; `services/publication.py` =
consentement + diff + persistance, **sans dépendance aiogram** ; `bot/routers/publish.py` = interaction.

**Scripts** : `scripts/calendar_state.py --describe` (lecture seule) / `--withdraw-all --confirm` ;
`scripts/publish_horizon.py --approve-for-test` (request→approve→publish, pour les scénarios quickstart).

## Tables SQLite

| Table | Description |
|-------|-------------|
| `users` | Compte Telegram, flags onboarding (`onboarding_completed_at`), préférences rappels (`reminders_enabled`, `reminder_hour`, `reminder_minute`, `reminder_last_sent_at`) |
| `athlete_profiles` | `profile` JSON → `AthleteProfileSchema` |
| `training_plans` | `plan_technical` JSON → `TrainingPlanSchema`, `start_date`, `is_active` |
| `session_logs` | `plan_id` NOT NULL, `tss_actual`, `rpe_emoji`, `logged_date`, `source_activity_id` (id intervals.icu) ; métriques qualité (`cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`, `session_type_real`, `variability_index`, `intensity_factor`, `dominant_zone`) ; contexte (`elevation_gain_m`, `average_temp_c`, `athlete_count`) |
| `chat_messages` | Historique LLM (role, content, intent, tool_used) |
| `activities` | Import historique (`source="intervals_icu"`, `source_activity_id`, `tss`, `tss_method`, `device_watts`) |
| `weekly_adherence` | Taux d'adhérence hebdomadaire — upsert à chaque `/recap` ; clé `(user_id, week_start_date)` ; colonnes : `sessions_done`, `sessions_planned`, `compliance_pct`, `tss_7d`, `week_number`, `plan_id` |
| `wellness` | HRV / FC repos / sommeil / CTL / ATL quotidiens — ingérée à chaque tick du poller (`ingest_wellness`), source de `get_current_fitness()` |
| `publication_approvals` | spec 005 — consentement enregistré et lié au contenu (`content_hash` SHA-256 sur ce qui a été montré) ; `status` pending/approved/declined (terminal, jamais supprimé — FR-003) ; `horizon_start`/`horizon_end`, `session_count` |
| `published_entries` | spec 005 — une ligne par séance écrite au calendrier ; `external_id` unique/user (`banister:<plan>:<date>:<slug>`), `intervals_event_id`, `approval_id` (FR-005), `content_hash`, `withdrawn_at` (gardée en historique — distingue « retirée par nous » de « supprimée par l'athlète ») |

`oauth_connections` a été supprimée (spec 002 T059) — l'authentification intervals.icu est une clé API
personnelle, pas un flux OAuth, donc aucune table de tokens n'est nécessaire.

Migrations : Alembic (`migrations/versions/`), appliquées automatiquement au démarrage
(`app/db/lifecycle.py::run_migrations()`) — jamais à la main. `init.sql` a été supprimé (spec 003 T050) ;
la baseline Alembic (`ecd6f700779f`) le remplace intégralement. Nouveau champ DB → `alembic revision
--autogenerate`, jamais un fichier SQL écrit à la main.

⚠️ Connu et accepté : une migration Alembic qui échoue en cours de route sur SQLite **ne s'annule pas**
automatiquement (contrairement à PostgreSQL) — SQLite ne supporte pas les DDL transactionnelles de la même
façon. En mono-utilisateur avec `scripts/backup.py` disponible, ce risque est accepté tel quel plutôt que
compensé par un mécanisme de sauvegarde automatique avant chaque migration.

## Schémas Pydantic clés

**`SessionSpec`** (séance dans le plan) :
```python
day_of_week: int          # 0=Lundi, 6=Dimanche
workout_type: str         # "long_ride" | "intervals" | "endurance" | "recovery"
zone_code: str            # "Z2", "Z4"...
duration_minutes: int
target_time_in_zone_minutes: int  # temps cible dans zone principale
tss_target: float
description_fr: str       # généré par app/engine/session_render.py, pas la seule description possible
steps: list[Step | RepeatGroup] | None = None  # spec 004 — None = séance "legacy" (plan pré-004)
```

**`Step`** / **`RepeatGroup`** (spec 004, `app/engine/schemas.py`) — structure d'une séance :
```python
Step: kind ("warmup"|"work"|"recovery"|"cooldown"|"steady"), duration_minutes, zone_code
RepeatGroup: repeat (≥2), steps: list[Step]  # une seule unité répétée, jamais imbriquée
```
Jamais de watts/bpm sur un `Step` — uniquement `zone_code`, relatif. `duration_minutes`/`zone_code`/
`target_time_in_zone_minutes` sont dérivés des steps (`derive_*()` dans `schemas.py`) et vérifiés par un
`model_validator` **uniquement quand `steps` est présent** — une séance sans steps garde son résumé stocké
tel quel (compat plans pré-004). `tss_target` n'est **pas** vérifié par ce validator (dérivation dépendante
de `coaching_mode`, une préoccupation de plan, pas de session) : voir `app/engine/tss.py::estimate_structured_session_tss()`.

**Bibliothèque de séances** (spec 004, `sessions/*.yaml`, chargée par `app/engine/session_library.py`) :
mirror du pattern `personas/*.yaml` — contenu éditable sans toucher au code. Chaque template porte
`purpose`/`intent`/`suits` obligatoires (FR-017) et une `structure` (steps). `scaling:` optionnel déclare
les bornes de `app/engine/fitting.py::fit_template()` — absent = template fixe, sélectionné tel quel.
Voir `sessions/README.md` et `specs/004-structured-workouts/contracts/session-library.md`.

**Convertir avant `build_system_prompt()`** :
```python
TrainingPlanSchema.model_validate(plan.plan_technical)  # pas l'ORM directement
```

## Règles importantes

### SQLAlchemy JSON
Mutations non auto-détectées → obligatoire :
```python
plan.plan_technical = new_data
flag_modified(plan, "plan_technical")
await session.flush()
```

### Telegram / parse_mode
- `parse_mode="HTML"` partout où les messages contiennent des underscores (identifiants, chemins)
- `parse_mode="Markdown"` uniquement si le texte est garanti sans underscore hors italique

### FSM aiogram
- `MemoryStorage` → états perdus au redémarrage — `UserLoaderMiddleware` restaure `PlanStates.ACTIVE` depuis la DB
- `FSMContext` inaccessible dans les middlewares (injecté après par aiogram)
- `chat_router` doit être enregistré en DERNIER dans `setup.py` (catch-all)

### Autorité de la source (spec 002, Constitution Principe IV)
- **Consommés tels quels, jamais recalculés** : charge d'entraînement de l'activité, CTL/ATL/TSB, zones
  puissance/FC, seuils FTP/LTHR
  - Par activité : `app/providers/intervals/mapper.py` (TSS, zones, seuils)
  - **CTL/ATL/TSB courants** (`/forme`, `/recap`, le chat, le message post-séance, le check KPI de
    surcharge) : `app/services/fitness.py::get_current_fitness()`, qui lit la table `wellness` — voir
    section ATL/CTL/TSB ci-dessous, le switch a été fait (spec 002 follow-up post-Phase 8)
- **Calculés localement** car la source ne les fournit pas : périodisation, génération/modification de
  plan, matching activité↔séance, KPI d'adhérence, projection de forme théorique
- `app/engine/tss.py` : `calc_tss()`/`calc_hrss()` (calcul par activité réelle) **supprimés** (spec 002
  T018/T019, leur dernier appelant réel — l'import historique Strava — a disparu en Phase 7).
  `estimate_session_tss*()`/`tss_from_weekly_hours()` (estimation pour la génération de plan, pas de
  calcul par activité réelle) **restent** — usage légitime, pas concerné par la règle d'autorité de la
  source. Idem `app/engine/zones.py` : reste, utilisé uniquement par `plan_builder.py`/`llm/tools.py`
  pour dériver les zones à afficher depuis le FTP déclaré, pas pour recalculer les zones d'une activité
- Le LLM ne calcule jamais aucune charge — voir principe fondateur en tête de ce document

### ATL/CTL/TSB
Consommés depuis la source pour la figure courante — `app/services/fitness.py::get_current_fitness()` lit
la table `wellness` (CTL/ATL quotidiens, y compris les jours de repos, contrairement à un CTL dérivé des
seules activités). Repli sur `compute_fitness_from_any()` (recalcul local) uniquement si aucune ligne
wellness n'existe encore — athlète tout juste connecté, avant le premier tick du poller.

⚠️ Trouvé en corrigeant ce switch : `app/providers/intervals/wellness.py::ingest_wellness()` et
`app/providers/intervals/history.py::import_history()` étaient tous les deux **construits, testés, et
jamais appelés nulle part dans l'app qui tourne** — la table `activities` était vide en conditions réelles
malgré Phase 5 marquée complète. Les deux sont maintenant appelés à chaque tick du poller (idempotents —
`import_history` se resume via `sync_state.history_import_complete`, `ingest_wellness` réingère une petite
fenêtre glissante à chaque tick, sans effet si déjà à jour). Écart réel mesuré sur le compte de test après
correction : TSB local à -36 (zone "surmenage") vs -18.8 selon la source (zone "fatigue normale") — 17
points d'écart, pas une nuance.

- `compute_fitness_from_any()` accepte liste mixte `SessionLog` + `Activity` (duck-typing) — reste le
  repli, et sert aussi `project_fitness_from_plan()` (projection théorique, jamais depuis la source)
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
- `intensity_factor` stocké en DB au moment de la notification poller (FTP de l'époque, immuable)
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

### Variable Reward — notification post-ride (`app/providers/analysis/highlight.py`)
- `select_highlight(analyzed, fitness, weekly_snap)` → `HighlightResult` — tirage pondéré parmi 6 catégories
- `detect_personal_records(all_logs, *, session_type_real, tss, intensity_factor, intervals_consistency_index, respect_zones_score, current_log_id)` → `PersonalRecord | None` — in-memory, pas de requête DB ; prend des primitives, pas un objet `AnalyzedSession` fabriqué (spec 002 T064)
- Notification en 3 messages : A (teaser, silencieux) → B (métrique héros, silencieux) → C (verdict + clavier RPE, seule vibration)
- Post-RPE : `edit_text` sur Message C → "Ton coach analyse..." → edit → récit LLM 3 phrases (aucune notification supplémentaire)

## Flux de configuration (`/setup`)

```
/setup → SPORT → GOAL → DATE → VOLUME → POWER → AGE → _finalize_setup() → plan
```

6 étapes, `SetupStates` dans `app/bot/states.py`, tout dans `app/bot/routers/setup.py` (~456 lignes).
Relancer `/setup` régénère le plan intégralement.

`_build_profile()` assemble l'`AthleteProfileSchema` ; si l'historique intervals.icu a été importé, la
forme actuelle (`current_ctl` / `current_atl` / `current_tsb`) est injectée depuis cet historique.

## Variables d'environnement

```
DATA_DIR=data                            # optionnel — chemin du dossier de données (SQLite)
DATABASE_URL=                            # optionnel — override, sinon SQLite dérivé de DATA_DIR
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_URL=https://<domaine>/webhook/telegram   # prod uniquement
INTERVALS_API_KEY=...                    # clé API personnelle intervals.icu (basic auth)
# INTERVALS_ATHLETE_ID=0                 # optionnel
INTERVALS_POLL_INTERVAL_MINUTES=5        # optionnel — défaut 5
LLM_PROVIDER=openrouter          # ou "anthropic"
LLM_MODEL=...
CHAT_MODEL=...
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...            # si LLM_PROVIDER=anthropic
```

## Navigation rapide

| Tâche | Fichier |
|-------|---------|
| Ajouter commande bot | `app/bot/routers/` + enregistrer dans `setup.py` avant `chat_router` |
| Modifier génération plan | `app/engine/plan_builder.py` |
| Modifier périodisation | `app/engine/periodization.py` |
| Ajouter/modifier une séance de la bibliothèque | `sessions/*.yaml` — voir `sessions/README.md`, aucun changement `.py` requis |
| Modifier le chargement/sélection de la bibliothèque | `app/engine/session_library.py` |
| Modifier le fitting (adapter un template à une charge cible) | `app/engine/fitting.py` — pas encore appelé par `generate_plan()` |
| Modifier la description d'une séance (texte, langue) | `app/engine/session_render.py` |
| Modifier la figure CTL/ATL/TSB courante (source) | `app/services/fitness.py` — `get_current_fitness()` |
| Modifier le calcul local ATL/CTL/TSB (repli, projection théorique) | `app/engine/atl_ctl.py` |
| Projection CTL théorique (suivi plan) | `app/engine/atl_ctl.py` — `project_fitness_from_plan()` |
| Modifier snapshot hebdo (monotonie, tendance) | `app/engine/weekly_snapshot.py` |
| Modifier récap hebdo (logique + LLM) | `app/services/weekly_recap.py` |
| Lire/écrire l'adhérence hebdomadaire | `app/db/repositories/weekly_adherence_repo.py` |
| Modifier le scheduler dimanche 20h | `app/main.py` — `_weekly_recap_scheduler()` |
| Modifier les rappels de séance (contenu message) | `app/main.py` — `_format_reminder()` |
| Modifier les rappels de séance (menu /reminders) | `app/bot/routers/reminders.py` |
| Ajouter un créneau horaire aux rappels | `app/bot/routers/reminders.py` — `_TIME_SLOTS` |
| Modifier feedback LLM post-séance | `app/llm/activity_analysis.py` |
| Modifier l'intervalle/logique du poller | `app/providers/intervals/poller.py` |
| Modifier le mapping payload intervals.icu → AnalyzedSession | `app/providers/intervals/mapper.py` |
| Modifier la notification post-ride (messages stagés, RPE) | `app/providers/intervals/notifier.py` — `send_staged_notification()` |
| Modifier le Variable Reward (catégories highlight, record perso) | `app/providers/analysis/highlight.py` — `select_highlight()`, `detect_personal_records()` |
| Modifier l'assemblage du contexte post-séance (sans dépendance bot) | `app/services/activity_feedback.py` — `assemble_activity_feedback()` |
| Modifier les modes narratifs LLM (journalist/analyst/coach) | `app/llm/prompts.py` — `build_narrative_system_prompt()` |
| Modifier le reveal post-RPE (edit-then-reveal) | `app/bot/routers/session_log.py` — `_reveal_activity_analysis()` |
| Modifier scoring matching activité ↔ plan | `app/providers/analysis/matching.py` — `score_activity_vs_session()` |
| Modifier fenêtre de matching / candidats | `app/providers/analysis/matching.py` — `find_plan_candidate()` |
| Modifier vue plan+réalisé pour le LLM (system prompt) | `app/llm/tools.py` — `build_activity_session_pairs()` + `_format_week_pairs()` |
| Ajouter outil LLM | `app/llm/tools.py` (définition JSON Schema) + `_execute_tool()` dans `app/llm/chat.py` |
| Modifier le rendu DSL d'une séance (texte envoyé à intervals.icu) | `app/providers/intervals/workout_dsl.py` — `render_dsl()` |
| Modifier le diff idempotent de publication (create/update/conflict) | `app/providers/intervals/calendar.py` — `publish_sessions()` |
| Modifier la barrière de consentement / le hash de plan | `app/services/publication.py` — `authorize_publication()`, `plan_content_hash()` |
| Modifier le texte de la demande d'approbation `/publish` | `app/services/publication.py` — `build_approval_request_text()` |
| Modifier la détection de divergence plan↔calendrier | `app/services/publication.py` — `check_divergence()` |
| Modifier le flux `/publish` / `/unpublish` (clavier, callbacks) | `app/bot/routers/publish.py` + `app/bot/keyboards/publish.py` |
| Ajouter champ DB | `app/db/models/` + `app/db/repositories/` + `alembic revision --autogenerate` |
| Architecture complète | `docs/ARCHITECTURE.md` |
