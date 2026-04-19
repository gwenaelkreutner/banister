# Banister — Architecture Technique

## Vue d'ensemble

Banister est un bot Telegram de coaching cyclisme. Il combine :
- une couche bot (aiogram v3) pour l'interface Telegram
- une couche API (FastAPI) pour les webhooks Telegram et OAuth Strava
- un moteur déterministe (Python) pour tous les calculs d'entraînement
- un LLM (OpenRouter/Anthropic) uniquement pour la génération de texte et le routing d'outils

```
Telegram
    │ messages / callbacks
    ▼
aiogram v3 (polling ou webhook)
    │ Dispatcher → Middlewares → Routers → Handlers
    ▼
FastAPI (même process)
    │ /webhook/telegram  /auth/strava/callback  /auth/strava/webhook
    ▼
SQLAlchemy AsyncSession → Supabase (PostgreSQL)
    +
LLM Provider (OpenRouter | Anthropic)
    +
Strava API (OAuth2 + activities)
```

**Point d'entrée unique** : `app/main.py`
- Crée le bot et le dispatcher au démarrage du module (niveau module, pas dans lifespan)
- Le lifespan FastAPI démarre le polling (dev) ou configure le webhook (prod)
- Sert simultanément les routes FastAPI et le bot aiogram

---

## Design Patterns

### 1. Repository
Chaque entité DB a un module dédié dans `app/db/repositories/`. Les handlers ne contiennent jamais de code SQL.

```
repo.user_repo.get_by_telegram_id(session, tid)
repo.plan_repo.get_active_plan(session, user_id)
repo.activity_repo.get_for_user(session, user_id, days=365)
```

Tous les repos exposent des fonctions async pures (pas de classes). Import centralisé via `app/db/repositories/__init__.py`.

### 2. Factory + Strategy (LLM)
`app/llm/factory.py` sélectionne le provider selon `settings.llm_provider` :

```python
get_provider()  # retourne OpenRouterProvider ou AnthropicProvider
```

Les deux providers implémentent la même interface :
```python
async def generate(system_prompt: str, user_message: str, max_tokens: int) -> str
```

Ajouter un nouveau provider = créer `app/llm/providers/newprovider.py` + case dans `factory.py`.

### 3. Middleware Chain (aiogram)
Ordre d'enregistrement dans `app/bot/setup.py` :

```
DbSessionMiddleware    → ouvre une AsyncSession + transaction (session.begin())
UserLoaderMiddleware   → upsert User depuis telegram_id + restaure PlanStates.ACTIVE
    ↓
Handler
```

Important : `DbSessionMiddleware` doit précéder `UserLoaderMiddleware` car celui-ci a besoin de la session.
`FSMContext` n'est **pas** accessible dans les middlewares (injecté après par aiogram).

### 4. Agentic Loop
`app/llm/chat_client.py` — `run_agentic_loop()` :

```
LLM call
  │
  ├── finish_reason == "tool_calls"
  │     → tool_executor(name, args) [code déterministe]
  │     → ajoute tool_result dans messages
  │     → itération suivante (max 2)
  │
  └── finish_reason != "tool_calls"
        → retourne (response_text, tool_used, last_tool_result)
```

Le LLM **ne calcule jamais** la charge — `tool_executor` appelle le moteur déterministe.

### 5. Orchestrator
`app/llm/chat.py` — `run_chat()` :
1. Charge le contexte (profil, plan, logs, historique conversation)
2. Construit system prompt + messages
3. Définit `tool_executor` (fermeture sur session/plan/profile/logs)
4. Appelle `run_agentic_loop()`
5. Déduit l'intent depuis l'outil appelé (`_intent_from_tool`)
6. Extrait la proposition de modification si applicable

### 6. State Machine (FSM aiogram)
États définis dans `app/bot/states.py` :

| Groupe | États |
|--------|-------|
| `OnboardingStates` | STRAVA_PIVOT, STRAVA_SHORT_GOAL, STRAVA_SHORT_DATE, STRAVA_SHORT_DAYS, STRAVA_SHORT_HEALTH, STEP_0 à STEP_N, DISCLAIMER |
| `PlanStates` | ACTIVE, PENDING_MODIFICATION |
| `SessionLogStates` | WAITING_RPE, WAITING_DURATION, WAITING_POWER, WAITING_HR |

**Transitions `PlanStates`** :
```
ACTIVE
  └─ message texte → run_chat()
       ├─ pas de proposal → réponse directe → ACTIVE
       └─ proposal (semaine ou séance) → stocké FSM data → PENDING_MODIFICATION + boutons
             ├─ [✅ Appliquer] → apply_proposed_modification() ou apply_session_adjustment()
             │                   → flag_modified + flush → ACTIVE
             └─ [❌ Annuler]  → ACTIVE
```

`PlanStates.ACTIVE` est restauré par `UserLoaderMiddleware` à chaque message si le plan existe.
`MemoryStorage` perd les états au redémarrage — la restauration vient de la DB.

Le callback `cb_apply_modification` dispatche selon `proposal["type"]` :
- `"session_adjustment"` → `apply_session_adjustment(plan, proposal)`
- (absent ou autre) → `apply_proposed_modification(plan, proposal)`

### 7. OAuth2 + HMAC CSRF (Strava)
`app/strava/oauth.py` :

```
state = f"{telegram_id}:{context}.{hmac_hex}"
         ─────────────────────────────────────
         context = "main" ou "onboarding"
         hmac_hex = HMAC-SHA256(secret, f"{telegram_id}:{context}")
```

`verify_state(state)` → `tuple[int, str]` (telegram_id, context) ou `None`

### 8. Lifespan Manager
`app/main.py` — `@asynccontextmanager async def lifespan(app)` :
- Si `settings.use_webhook` → `bot.set_webhook()` au démarrage
- Sinon → `asyncio.create_task(dp.start_polling(bot))` (dev, polling en background)
- `asyncio.create_task(_weekly_recap_scheduler(bot))` — toujours actif (polling ou webhook)
- Cleanup : cancel recap_scheduler_task + cancel polling task + fermer session bot

### 9. Scheduler asyncio (récap hebdomadaire)
`app/main.py` — `_weekly_recap_scheduler(bot)` :

Aucune dépendance externe (pas d'APScheduler). Boucle pure asyncio :
```
while True:
    calcule le délai jusqu'au prochain dimanche 20h00 UTC
    await asyncio.sleep(délai_secondes)
    await _run_weekly_recap_broadcast(bot)
```

`_run_weekly_recap_broadcast(bot)` :
- Ouvre une session courte pour récupérer tous les `User` actifs avec onboarding terminé
- Pour chaque utilisateur : ouvre une session fraîche → appelle `compute_weekly_recap()` → envoie 3 messages
- **Session par utilisateur** (pas une session globale) — évite la saturation du pool Supabase
- Rate limit : `asyncio.sleep(0.1)` entre chaque utilisateur

**Comportement au redémarrage** : si le process redémarre après 20h dimanche, le prochain envoi est la semaine suivante (acceptable MVP — la commande `/recap` reste disponible immédiatement).

### 10. Couche Services (`app/services/`)
Couche intermédiaire entre les routers (présentation) et les repositories (accès DB).
Orchestrent plusieurs repos + appels LLM. N'ont pas de dépendance sur aiogram.

```
app/services/
└── weekly_recap.py   → compute_weekly_recap(session, user) → WeeklyRecapResult
```

Appelable depuis :
- Le handler `/recap` (session injectée par `DbSessionMiddleware`)
- Le scheduler (session ouverte par le broadcast)

---

## Couche Bot (aiogram v3)

### Ordre des routers (app/bot/setup.py)
```
common_router            → /start /cancel /help
onboarding_strava_router → pivot Strava + flow court 4 questions
onboarding_router        → flow classique 12 étapes
disclaimer_router        → disclaimer + génération plan
plan_router              → /plan /week N
strava_router            → /connect_strava /disconnect_strava
session_log_router       → logging RPE post-sortie
forme_router             → /forme (ATL/CTL/TSB)
recap_router             → /recap (bilan hebdomadaire)
chat_router              → catch-all (DOIT être en dernier)
```

Le `chat_router` utilise `StateFilter(PlanStates.ACTIVE, None)` — il capture **tous** les messages sans commande. C'est pourquoi il doit être enregistré en dernier.

### Keyboards
Les callbacks inline utilisent des prefixes distincts :
- `onboard_` → `app/bot/keyboards/onboarding.py`
- `plan_` → `app/bot/keyboards/plan.py`
- `log_` → `app/bot/keyboards/session_log.py`
- `strava:` → callbacks Strava dans `onboarding_strava.py`
- `chat:` → confirmation modification plan dans `chat.py`

### Middlewares en détail

**DbSessionMiddleware** (`app/bot/middlewares/db_session.py`) :
```python
async with AsyncSessionFactory() as session:
    async with session.begin():   # transaction auto-commit/rollback
        data["session"] = session
        return await handler(event, data)
```

**UserLoaderMiddleware** (`app/bot/middlewares/user_loader.py`) :
- Upsert `User` depuis `telegram_id` (crée si absent)
- Si `user.onboarding_completed` et plan actif → restaure `PlanStates.ACTIVE`
- Injecte `user` dans `data["user"]`

---

## Couche DB (SQLAlchemy async + Supabase)

### Connexion
`app/db/client.py` :
```python
AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)
```
URL : `postgresql+asyncpg://...?ssl=require` (SSL obligatoire sur Supabase)

### Modèles ORM
| Fichier | Table | Champs clés |
|---------|-------|-------------|
| `user.py` | `users` | `telegram_id`, `first_name`, `onboarding_completed` |
| `profile.py` | `athlete_profiles` | `profile` (JSONB → AthleteProfileSchema) |
| `training_plan.py` | `training_plans` | `plan_technical` (JSONB → TrainingPlanSchema), `start_date`, `is_active` |
| `onboarding.py` | `onboarding_state` | `current_step`, `session_data` (JSONB) |
| `oauth_connection.py` | `oauth_connections` | `provider`, `access_token`, `refresh_token`, `expires_at` |
| `session_log.py` | `session_logs` | `plan_id` (NOT NULL), `tss_actual`, `rpe_emoji`, `logged_date` ; métriques qualité (`cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`, `session_type_real`, `variability_index`, `intensity_factor`, `dominant_zone`) ; contexte Strava (`elevation_gain_m`, `average_temp_c`, `athlete_count`) |
| `chat_message.py` | `chat_messages` | `role`, `content`, `intent`, `tool_used` |
| `activity.py` | `activities` | `source_activity_id`, `tss`, `tss_method`, `device_watts` |
| `weekly_adherence.py` | `weekly_adherence` | clé `(user_id, week_start_date)` — upsert à chaque `/recap` ; `sessions_done`, `sessions_planned`, `compliance_pct`, `tss_7d`, `week_number`, `plan_id` |
| `session_log.py` | `session_logs` | ... `kpi_contribution FLOAT` — points KPI gagnés pour cette séance (NULL si non calculé, négatif si surcharge) |

### JSONB — piège courant
Les mutations de champs JSONB ne sont pas auto-détectées par SQLAlchemy :
```python
plan.plan_technical = new_data
flag_modified(plan, "plan_technical")   # obligatoire
await session.flush()
```
Idem pour `profile.profile`.

### Migrations
Fichiers SQL dans `migrations/` à exécuter manuellement dans Supabase SQL Editor.
`001_add_oauth_connections.sql` → `014_add_kpi_contribution.sql`

`014_add_kpi_contribution.sql` : ajoute `session_logs.kpi_contribution FLOAT` pour le KPI d'adhérence.

---

## Couche LLM

### chat_client.py — run_agentic_loop()
- Utilise le SDK OpenAI avec `base_url="https://openrouter.ai/api/v1"`
- Modèle par défaut : `settings.chat_model` (configurable via `CHAT_MODEL` env var)
- `max_iterations=2` : le LLM ne peut appeler qu'un seul outil par conversation
- Fallback si itérations épuisées : appel final sans tools

### tools.py — TOOL_DEFINITIONS
5 outils disponibles pour le LLM :
```
get_fitness_data           → retourne ATL/CTL/TSB + 7 dernières séances
get_upcoming_sessions      → retourne les séances du plan (n prochains jours)
update_injury_status       → enregistre blessure + adapte plan (effet immédiat)
propose_plan_modification  → propose ajustement d'une semaine entière (nécessite confirmation)
propose_session_adjustment → propose ajustement d'une séance précise (nécessite confirmation)
```

**Routing LLM (règle de choix d'outil)** injectée dans `build_system_prompt()` :
- Contrainte sur **un seul jour** (réunion, météo, fatigue du jour) → `propose_session_adjustment`
- Contrainte sur **toute une semaine** (fatigue chronique, semaine chargée) → `propose_plan_modification`

`build_system_prompt()` injecte : profil athlète, métriques, plan en cours, séances récentes.
Quand `session_logs` est fourni, la vue "SEMAINE EN COURS" affiche les paires plan+réalisé formatées par `_format_week_pairs()` (via `build_activity_session_pairs()`) — avec ✅/❌/📅/🔄, dates réelles, TSS delta, `⚠️` si zone non respectée.
`build_context_messages()` convertit l'historique DB en format messages OpenAI.

### narrator.py
Génère le résumé narratif d'une semaine d'entraînement (affiché dans `/plan`).
Appelle `provider.generate()` — aucun calcul, texte uniquement.

---

## Couche Strava

### OAuth flow
```
/connect_strava (bot)
    → build_auth_url(telegram_id, context="main")
    → [utilisateur autorise sur strava.com]
    → GET /auth/strava/callback?code=...&state=...
    → verify_state() → (telegram_id, context)
    → exchange_code(code) → tokens
    → oauth_repo.upsert_connection()
    → bot.send_message(telegram_id, "✅ Strava connecté !")
```

Si `context="onboarding"` → lance `_run_strava_onboarding_import()` en background task.

### import_history (strava/history.py)
```python
analysis = await import_history(session, user_id, access_token)
```
3 appels API Strava :
1. `get_athlete()` → sexe, poids, pays
2. `get_athlete_stats()` → totaux YTD
3. `get_activities(weeks=7)` → dernières activités (49 jours)

Calcule TSS par activité (priorité power > HR > estimation).
`_analyze()` retourne : `level_detected`, `volume_suggested`, `has_power_meter`, `ftp_detected`, `hr_max_detected`, jours préférés, etc.

### Webhook Strava
- `GET /auth/strava/webhook` → validation challenge (subscription setup)
- `POST /auth/strava/webhook` → événements activité
- Traitement via `asyncio.create_task()` (Strava exige < 2s de réponse)

---

## Navigation dans le code — guide rapide

| Tâche | Où aller |
|-------|----------|
| Ajouter une commande bot | Créer `app/bot/routers/ma_commande.py`, enregistrer dans `setup.py` avant `chat_router` |
| Ajouter un outil LLM | `app/llm/tools.py` (définition JSON Schema + impl. `_tool_xxx`) + `app/llm/chat.py` `_execute_tool()` |
| Modifier le calcul TSS | `app/engine/tss.py` |
| Modifier la génération de plan | `app/engine/plan_builder.py` |
| Modifier les règles de périodisation | `app/engine/periodization.py` |
| Modifier ATL/CTL/TSB | `app/engine/atl_ctl.py` |
| Modifier snapshot hebdo (monotonie, tendance) | `app/engine/weekly_snapshot.py` |
| Modifier le KPI d'adhérence (formule, seuils, affichage) | `app/engine/adherence_kpi.py` |
| Modifier le rendu KPI dans le message post-séance | `app/bot/routers/session_log.py` — `cb_rpe_strava()` / `cb_rpe_manual()` + `_reveal_activity_analysis()` |
| Modifier le récap hebdomadaire (logique + LLM) | `app/services/weekly_recap.py` |
| Lire/écrire l'adhérence hebdomadaire | `app/db/repositories/weekly_adherence_repo.py` |
| Modifier le handler /recap (format messages) | `app/bot/routers/recap.py` |
| Modifier les prompts LLM du récap | `app/llm/prompts.py` — `WEEKLY_RECAP_*` |
| Modifier le scheduler dimanche 20h | `app/main.py` — `_weekly_recap_scheduler()` |
| Modifier feedback LLM post-séance | `app/llm/activity_analysis.py` |
| Modifier la notification post-ride (séquence 3 messages) | `app/strava/webhook.py` — `_notify_staged_rpe_request()` |
| Modifier notification "sortie bonus" (slot déjà pris) | `app/strava/webhook.py` — `_notify_bonus_activity()` |
| Modifier notification "activité hors plan" | `app/strava/webhook.py` — `_notify_unplanned()` |
| Modifier le highlight / Variable Reward | `app/strava/highlight.py` — `select_highlight()` (catégories + poids) |
| Modifier les modes narratifs LLM (journalist/analyst/coach) | `app/llm/prompts.py` — `build_narrative_system_prompt()` |
| Modifier le reveal post-RPE (edit-then-reveal) | `app/bot/routers/session_log.py` — `_reveal_activity_analysis()` |
| Modifier analyse activité (zones, NP, IF, session_type_real) | `app/strava/analyzer.py` — `SessionAnalyzer` |
| Modifier scoring matching activité ↔ plan | `app/strava/matching.py` — `score_activity_vs_session()` |
| Modifier fenêtre de matching / candidats | `app/strava/matching.py` — `find_plan_candidate()` |
| Modifier vue plan+réalisé dans le system prompt | `app/llm/tools.py` — `build_activity_session_pairs()` + `_format_week_pairs()` |
| Ajouter un outil LLM | `app/llm/tools.py` (définition JSON Schema) + `_execute_tool()` dans `app/llm/chat.py` |
| Ajouter un champ DB | `app/db/models/` + `app/db/repositories/` + fichier migration `migrations/00N_...sql` |
| Changer le système prompt chat | `app/llm/tools.py` — `build_system_prompt()` |
| Changer le provider LLM | `.env` : `LLM_PROVIDER=anthropic` ou `LLM_PROVIDER=openrouter` |
| Déboguer les FSM states | Vérifier `UserLoaderMiddleware` (restauration) + `StateFilter` dans le handler |

---

## Variables d'environnement

```
DATABASE_URL=postgresql+asyncpg://postgres:<pwd>@<host>/postgres?ssl=require
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_URL=https://<domaine>/webhook/telegram   # prod uniquement
TELEGRAM_WEBHOOK_SECRET=<secret>
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_REDIRECT_URI=https://<domaine>/auth/strava/callback
STRAVA_STATE_SECRET=<secret aléatoire>
STRAVA_WEBHOOK_VERIFY_TOKEN=<secret>
LLM_PROVIDER=openrouter          # ou "anthropic"
LLM_MODEL=arcee-ai/trinity-large-preview:free
CHAT_MODEL=arcee-ai/trinity-large-preview:free
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...            # si LLM_PROVIDER=anthropic
LOG_LEVEL=INFO
```

Démarrage dev : `python -m uvicorn app.main:app --port 8000 --reload`

## Pipeline d'analyse Strava (nouveau)

Pour décorréler l'API Strava de l'objet métier, un pipeline en 3 couches est introduit :

1. `app/strava/fetcher.py` — `StravaActivityFetcher`
   - Appelle systématiquement `getActivity`
   - Applique la logique de tiers pour `getActivityStreams` :
     - Tier 3 : séance planifiée ou course/test → streams complets
     - Tier 2 : ride/run > 20 min avec power ou HR → streams allégés
     - Tier 1 : autres activités → pas de streams
   - Retourne `RawActivity` sans calcul métier

2. `app/strava/analysis_models.py`
   - `RawActivity` / `RawActivityStreams` : DTO d'ingestion
   - `AnalyzedSession` : objet métier agnostique de la source Strava

3. `app/strava/analyzer.py` — `SessionAnalyzer`
   - Transforme `RawActivity` en `AnalyzedSession`
   - Calcule le temps en zones depuis les streams (power puis HR)
   - Délègue le calcul TSS à la fonction existante `_calc_tss`
   - Attribue un `session_type_real` via règles simples (endurance/tempo/intervals/...)
   - Expose des métriques avancées : `cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`, `session_type_real`, `dominant_zone`, `variability_index` (NP / avg_power)
   - Détermine `environment: "indoor" | "outdoor" | None` depuis `strava_sport_type == "VirtualRide"`
   - `RawActivity` inclut les champs de contexte extérieur : `average_temp`, `total_elevation_gain`, `athlete_count`

Intégration actuelle : le webhook `handle_activity_event` consomme `StravaActivityFetcher` + `SessionAnalyzer` et passe directement `AnalyzedSession` au matching — aucune conversion legacy.

### Cas d'appel Streams (détaillé)

Le comportement réel se comprend en 2 temps :

1) **Fetch initial** (`fetch_raw_activity`) selon tiers
- **Tier 1**: pas de streams
- **Tier 2**: streams légers (`time`, `watts`, `heartrate`)
- **Tier 3**: streams complets (`time`, `watts`, `heartrate`, `velocity_smooth`, `grade_smooth`, `moving`, `altitude`)

2) **Upgrade éventuel post-matching** (`ensure_tier3_streams`)
- Si la séance est alignée avec le plan, on vérifie si les streams Tier 3 sont déjà présents.
- Si incomplets, un appel supplémentaire `getActivityStreams` est fait pour compléter.
- Si déjà complets, **pas** de second appel.

Conséquence importante : selon les cas, il peut y avoir **0, 1 ou 2 appels streams**.

| Cas | Appel streams initial | Upgrade post-match | Total appels streams |
|---|---:|---:|---:|
| Activité courte/non pertinente (Tier 1), non alignée | 0 | 0 | 0 |
| Activité standard (Tier 2), non alignée | 1 (léger) | 0 | 1 |
| Activité standard (Tier 2), alignée plan | 1 (léger) | 1 (Tier 3) | 2 |
| Activité déjà Tier 3, alignée plan | 1 (complet) | 0 (déjà complet) | 1 |

### Priorité des watts normalisés (NP) et impact TSS

La priorité métier retenue est :

1. `weighted_average_watts` de la plateforme (source canonique si disponible)
2. NP calculé via streams watts (formule puissance 4)
3. si indisponible: `None` (pas d'estimation artificielle)

Cette priorité est appliquée dans l'analyzer pour le calcul de `AnalyzedSession.normalized_power`.

### Séquence webhook (pour éviter les ambiguïtés)

1. Fetch initial + première analyse
2. Construction de `used_slots` depuis les logs "done" existants du plan → `frozenset[(week_number, day_of_week)]`
3. Matching sémantique avec le plan (`evaluate_activity_plan_match(plan, analyzed, activity_date, used_slots=used_slots)`)
4. **3 branches selon le résultat** :
   - `is_aligned=True` → upgrade Tier 3 + ré-analyse + persistance `session_logs` + `create_task(_notify_staged_rpe_request(...))`
   - `all_slots_taken=True` → persistance comme "unplanned" + `create_task(_notify_bonus_activity(...))` — "🔄 Sortie bonus"
   - aucun candidat → persistance comme "unplanned" + `create_task(_notify_unplanned(...))` — "📊 Activité hors plan"
5. (branche alignée uniquement) Calcul ATL/CTL/TSB + sélection du highlight → inclus dans `_notify_staged_rpe_request`

La notification est déclenchée **après** la fermeture de la session DB pour permettre les `asyncio.sleep()` sans bloquer le pool de connexions.

### Matching sémantique (`app/strava/matching.py`)

**Principe** : l'activité est comparée à toutes les séances de sa semaine d'entraînement (relative à `plan.start_date`, tolérance ±2j) et on retient celle qui lui ressemble le plus — pas forcément la plus proche temporellement.

Score sur 100 pts :
| Dimension | Pts | Critère |
|-----------|-----|---------|
| Durée | 30 | ratio `elapsed / planned` dans ±10% = plein score ; décroissance linéaire jusqu'à 0 à ±75% (pas de plafond fixe) |
| Charge TSS | 30 | ratio `actual / target` dans ±30% = plein score |
| Type séance | 25 | matrice `session_type_real × workout_type` (7×4) |
| Zone dominante | 5 | bonus si `dominant_zone == zone_code` planifié |
| Contexte indoor/outdoor | 10 | indoor pour intervals/recovery, outdoor pour long_ride |

Pénalité temporelle : **-2 pts par jour de décalage** (`|day_shift| × 2`) appliquée avant comparaison des candidats — favorise le jour exact sans bloquer le matching sémantique.

Seuil de match : `confidence_score >= 50`. Tie-break par `|day_shift|` minimal à score ajusté égal.

`used_slots: frozenset[(week_number, day_of_week)]` — slots déjà pris par d'autres activités "done" du même plan, ignorés lors de la recherche de candidats.

`build_activity_session_pairs(plan_schema, plan_start_date, session_logs, week_number)` — utilitaire pour le LLM : construit les paires (séance planifiée ↔ activité réalisée) d'une semaine, incluant les séances manquées et les bonus. Utilisé dans `build_system_prompt()` via `_format_week_pairs()` pour afficher la vue plan+réalisé.

---

## Variable Reward — Notification post-ride stagée (`app/strava/highlight.py`)

La notification post-ride suit une séquence en 3 messages pour créer anticipation et récompense variable.

### Séquence (3 messages + 1 édition)

```
t=0s    send_chat_action("typing") → sleep 1.2s
t=1.2s  Message A — disable_notification=True   ← accroche sans vibration
        send_chat_action("typing") → sleep 0.8s
t=2.0s  Message B — disable_notification=True   ← métrique héros sans vibration
        send_chat_action("typing") → sleep 1.0s
t=3.0s  Message C — notification normale         ← seule vibration téléphone
        (verdict plan + CTL/ATL/TSB + prompt RPE + clavier)

Clic RPE → edit_text("Ton coach analyse...")     ← aucune nouvelle notification
1.5s     → edit_text("<analyse LLM 3 phrases>")  ← aucune nouvelle notification
```

Règle push : **1 seule vibration** sur toute la séquence (Message C). Les Messages A et B sont déposés silencieusement ; les éditions post-RPE ne pushent jamais (comportement Telegram natif).

### `select_highlight()` — Variable Reward

Six catégories pondérées. `random.choices(candidates, weights)` → même performance, angle différent à chaque fois :

| Catégorie | Condition | Poids |
|-----------|-----------|-------|
| `CONSISTENCY_KING` | `intervals_consistency >= 0.88` | 1.4 |
| `POWER_PEAK` | `intensity_factor >= 0.95` | 1.3 |
| `CARDIAC_STORY` | `abs(cardiac_drift) >= 8%` | 1.2 |
| `ZONE_DISCIPLINE` | `respect_zones >= 90` ou `<= 60` | 1.0 |
| `TSB_SIGNAL` | `tsb >= 10` ou `tsb <= -25` | 0.9 |
| `VOLUME_CONTEXT` | toujours vrai (fallback) | 0.8 |

### `detect_personal_records()` — In-memory, pas de requête DB

Appelé dans `cb_rpe_strava()` après la soumission RPE (le log est alors committé). Filtre `all_logs` par `session_type_real` identique, compare sur : `tss_actual`, `intensity_factor`, `intervals_consistency_index`, `respect_zones_score`. Requiert ≥ 3 séances dans le pool de comparaison.

### Flux post-RPE — edit-then-reveal

`cb_rpe_strava()` (dans `session_log.py`) :
1. `edit_text("✅ Ressenti noté.\n\n🔍 Ton coach analyse...")` — supprime le clavier, état loading
2. `asyncio.create_task(_reveal_activity_analysis(...))` avec `storytelling_mode`, `highlight_category`, `personal_record`
3. `_reveal_activity_analysis()` : typing → sleep 1.5s → `edit_text("<analyse LLM>\n\n<kpi_block>")`

**`kpi_block`** (optionnel, `str | None`) : bloc KPI formaté Telegram HTML généré par `compute_kpi_display()`.
Transmis via paramètre `kpi_block=` dans `_reveal_activity_analysis()` et `_send_activity_analysis()`.
Contenu : delta pts, progress bar ASCII, message de rythme, milestone si franchissement de seuil.

---

## Snapshot hebdomadaire (`app/engine/weekly_snapshot.py`)

Fonction pure, sans requête DB. Prend la liste `logs` déjà en mémoire.

```python
WeeklySnapshot(
    tss_7d,           # TSS total des 7 derniers jours [today-6 .. today]
    tss_6w_avg,       # TSS hebdo moyen des 6 semaines complètes précédentes
    load_trend_pct,   # (tss_7d - tss_6w_avg) / tss_6w_avg × 100
    sessions_done_7d, # séances "done" dans la fenêtre 7j
    monotony_index,   # Formule Foster : mean(TSS journaliers) / std — None si std=0 ou <2j
)
```

**Formule Foster** : `mean / std` des TSS journaliers de la fenêtre 7j.
- Score > 2.0 = charge uniforme = risque physiologique (manque de variété).
- `None` si std=0 (charge parfaitement constante) ou < 2 jours d'entraînement.

**Fenêtre 6 semaines** : les 6 semaines complètes **avant** `today-6` — pas de chevauchement avec la fenêtre courante.

Utilisé dans `app/bot/routers/session_log.py` au moment du RPE pour enrichir `generate_activity_analysis()`.

---

## Analyse LLM post-séance enrichie (`app/llm/activity_analysis.py`)

`generate_activity_analysis(**kwargs)` — prompt structuré en 5 blocs conditionnels :

```
[MÉTRIQUE EN VEDETTE]  highlight_category (si fourni) — oriente le LLM sur l'angle principal
[RECORD PERSONNEL]     personal_record (si fourni) — moment mémorable à mentionner
[SÉANCE]       durée, TSS réalisé, delta plan (flag ✅/⚠️/📊), RPE, alerte RPE/FTP
[PUISSANCE]    avg_power, NP, IF, VI (si duration ≥ 30 min)
[QUALITÉ]      session_type_real, zones (distribution + respect), drift cardiaque, consistance intervalles
[CONTEXTE]     dénivelé, température, groupe (athlete_count > 1)
[FORME & CHARGE] CTL, ATL, TSB + directive tonale + snapshot hebdo (tss_7d, tendance %, séances, monotonie)
```

**Paramètres optionnels Variable Reward** (défaut `None` → comportement classique) :
- `highlight_category: str | None` — catégorie de la métrique mise en avant dans Message B
- `personal_record: dict | None` — PersonalRecord sérialisé si record détecté
- `storytelling_mode: str | None` — `"journalist"` | `"analyst"` | `"coach"`

**3 modes narratifs (arc à 3 phrases exactes)** — system prompt via `build_narrative_system_prompt()` :
| Mode | Structure |
|------|-----------|
| `journalist` | Accroche sur le fait le plus surprenant → interprétation → implication future |
| `analyst` | Métrique principale + valeur → corrélation contextuelle → recommandation mesurable |
| `coach` | Validation spécifique (avec chiffre) → enseignement → élan vers la prochaine séance |

Quand `storytelling_mode=None` (flux manuel `/log`) : comportement classique avec structure explicite 4-5 phrases et `build_ux_system_prompt()`.

**Directive tonale (TSB-driven)**, alignée sur `tsb_label()` :
- `tsb < -30` → `"ton protecteur — récupération prioritaire"`
- `tsb >= +5`  → `"ton motivant — souligne la progression"`
- sinon        → `"coaching équilibré et pédagogique"`

**Règles d'affichage** :
- `variability_index` : ignoré si `duration_minutes < 30` (non représentatif, stops urbains)
- `intensity_factor` : lu depuis `session_log.intensity_factor` (stocké au webhook, FTP immuable)
- `max_tokens = 300`

**Helpers déterministes** (testables sans LLM) :
- `_compute_match_score(planned_tss, actual_tss)` → `(flag, text)` — ✅/⚠️/📊
- `_detect_rpe_mismatch(rpe, actual_tss, planned_tss)` → suggestion ou None
- `_tsb_tone(tsb)` → directive textuelle

