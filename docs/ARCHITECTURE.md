# Banister — Architecture technique

> Ce document décrit la **forme du système** : le modèle de process, les couches et
> leurs responsabilités, les flux de données. Pour l'historique spec par spec, les
> règles métier détaillées et la table de navigation « quelle tâche → quel fichier »,
> voir `CLAUDE.md` (référence vivante, gouvernée par
> `.specify/memory/constitution.md` v1.1.0). État décrit ici : refonte open source
> **terminée** (specs 001–007).

---

## 1. Vue d'ensemble

Banister est un bot Telegram de coaching cyclisme **auto-hébergeable, mono-utilisateur**.
Un athlète connecte son compte intervals.icu ; le bot génère un plan d'entraînement,
suit les sorties réelles, adapte le plan à la demande, signale les dérives de charge,
et — sur approbation explicite — pousse les séances vers le calendrier intervals.icu.

Quatre briques dans **un seul process Python** :

| Brique | Rôle | Techno |
|--------|------|--------|
| Couche bot | Interface Telegram (commandes, claviers, FSM) | aiogram v3 (async) |
| Couche API | Sert **uniquement** le webhook Telegram en prod | FastAPI |
| Moteur déterministe | **Tous** les calculs de charge, périodisation, matching | Python pur, zéro I/O |
| Couche LLM | Rédaction de texte + routing d'outils — **jamais de calcul** | OpenRouter \| Anthropic |

```
                          Telegram
                             │  messages / callbacks
                             ▼
            ┌────────────────────────────────────┐
            │  aiogram v3   (polling en dev,     │
            │               webhook en prod)     │
            │  Dispatcher → Middlewares → Routers │
            └───────────────┬────────────────────┘
                            │
        ┌───────────────────┼───────────────────────┐
        ▼                   ▼                       ▼
  app/engine/         app/services/            app/llm/
  moteur pur      orchestration (repos+LLM)   rédaction + outils
        │                   │                       │
        └─────────┬─────────┘                       │
                  ▼                                 │
          app/db/repositories/  ──►  SQLite local (fichier unique)
                                                    │
        app/providers/intervals/  ◄─────────────────┘
        lecture activités/wellness/profil,
        écriture calendrier (spec 005)
                  │
                  ▼
            intervals.icu API  (clé API personnelle, basic auth)
```

**Point d'entrée** : `app/main.py`
- Bot + Dispatcher créés au niveau module.
- Le `lifespan` FastAPI applique les migrations Alembic, puis démarre — en tâches
  `asyncio` de fond — le polling aiogram (dev), le poller intervals.icu, le scheduler
  de récap hebdo et le scheduler de rappels de séance.
- Un seul endpoint HTTP entrant : `POST /webhook/telegram` (prod) + `GET /health`.
  **Aucun endpoint entrant côté source de données** — décision verrouillée spec 001
  (les webhooks intervals.icu exigent d'enregistrer une application et ajoutent un
  délai de consolidation). C'est ce qui rend l'auto-hébergement trivial : pas de
  domaine, pas de certificat, pas de reverse proxy en dev.

---

## 2. Principe fondateur — la frontière du calcul

**Le LLM ne calcule jamais la charge d'entraînement.** Toute valeur numérique
(TSS, CTL/ATL/TSB, zones, ramp rate, ratios) vient soit de la source (intervals.icu),
soit du moteur déterministe (`app/engine/`). Le LLM reçoit ces nombres déjà calculés
et les met en récit.

Renforcé en spec 006 : **les chiffres que le LLM énonce sont vérifiés avant envoi**
(`app/services/response_verification.py`) — une affirmation numérique qui ne
correspond pas à ce qui a été retrouvé est retirée phrase par phrase et enregistrée.

**Autorité de la source** (Constitution Principe IV) :
- *Consommés tels quels, jamais recalculés* : charge par activité, CTL/ATL/TSB, zones
  puissance/FC, seuils FTP/LTHR.
- *Calculés localement* car la source ne les fournit pas : périodisation, génération
  et modification de plan, matching activité↔séance, KPI d'adhérence, projection de
  forme théorique, garde-fous.
- Si la source est injoignable : l'ancienneté de la donnée est annoncée, jamais
  remplacée par une estimation présentée comme actuelle.

---

## 3. Couches et responsabilités

### `app/bot/` — présentation Telegram
- `setup.py` : Dispatcher + middlewares + ordre d'enregistrement des routers
  (`chat_router` **toujours en dernier** — c'est le catch-all des messages sans commande).
- `middlewares/db_session.py` : ouvre une `AsyncSession` par update — **doit précéder**
  `single_user.py`.
- `middlewares/single_user.py` : garde `TELEGRAM_OWNER_ID`, injecte le `User`
  (pas d'upsert — mono-utilisateur).
- `routers/` : un fichier par domaine de commande. Préfixes de callbacks distincts :
  `setup:` `plan:` `log:` `chat:` `rem:` `pub:` `goal:` `voice:`.
- `states.py` : groupes FSM (`SetupStates`, `PlanStates`, `GoalStates`, `ResetStates`).
  `MemoryStorage` → états perdus au redémarrage ; `PlanStates.ACTIVE` est restauré
  depuis la DB par le middleware.

### `app/services/` — orchestration
Couche intermédiaire entre routers (présentation) et repositories (DB). Combine
plusieurs repos + appels LLM. **Aucune dépendance aiogram** → testable et réutilisable
depuis les schedulers.

| Service | Rôle |
|---------|------|
| `fitness.py` | `get_current_fitness()` — CTL/ATL/TSB courants lus depuis la table `wellness` |
| `weekly_recap.py` | `compute_weekly_recap()` → `WeeklyRecapResult` |
| `activity_feedback.py` | contexte post-séance assemblé, sans dépendance bot |
| `publication.py` | spec 005 — barrière de consentement, diff plan↔calendrier, retrait |
| `guardrail_service.py` | spec 006 — assemble les `GuardrailFinding` ; **aucun chemin d'écriture** |
| `response_verification.py` | spec 006 — `MetricRegistry` + vérification des chiffres de la réponse LLM |
| `coach_voice.py` | spec 007 — `resolve_voice(user)` : chaîne de fallback de persona |

### `app/engine/` — moteur déterministe (Python pur, zéro LLM, zéro I/O)

| Module | Rôle |
|--------|------|
| `schemas.py` | Pydantic : `AthleteProfileSchema`, `TrainingPlanSchema`, `SessionSpec`, `Step`, `RepeatGroup` |
| `periodization.py` | Séquence de blocs Base / Build / Peak / Taper |
| `plan_builder.py` | `generate_plan()` — plan complet ; sélectionne dans `session_library.py` |
| `plan_modifier.py` | Outil LLM de modification ; préserve/adapte les steps |
| `session_library.py` | Charge/valide `sessions/*.yaml`, sélection déterministe (spec 004) |
| `fitting.py` | `fit_template()` — adapte un template à une cible de charge (spec 004 ; **pas encore branché à `generate_plan()`** — voir §7) |
| `session_render.py` | Description d'une séance dérivée de ses steps, paramétrée par langue |
| `atl_ctl.py` | ATL/CTL/TSB local (EMA τ=7j/42j) — **repli** + projection théorique du plan |
| `weekly_snapshot.py` | Tendance de charge, monotonie Foster (corrigée spec 006) |
| `guardrails.py` | spec 006 — évaluateurs purs (ACWR, ramp, monotonie, VFC, FC repos) → `GuardrailFinding` |
| `baselines.py` | spec 006 — baselines glissantes personnelles |
| `guardrail_thresholds.py` | spec 006 — tous les seuils + leur source publiée (lu comme de la doc) |

`plan_builder.generate_plan(profile) -> TrainingPlanSchema` est le point d'entrée du
moteur. Aucun accès DB : on lui passe un `AthleteProfileSchema`, il retourne un
`TrainingPlanSchema`.

### `app/llm/` — rédaction et outils

| Module | Rôle |
|--------|------|
| `factory.py` | Sélection du provider (`openrouter` \| `anthropic`) |
| `providers/` | `anthropic.py`, `openrouter.py` — interface commune `generate(system, user, max_tokens)` |
| `chat_client.py` | `run_agentic_loop()` — SDK OpenAI + base_url OpenRouter, **max 2 itérations d'outils** |
| `chat.py` | `run_chat()` → `(text, intent, tool_used, pending_proposal)` |
| `tools.py` | 5 outils LLM (JSON Schema) + `build_system_prompt()` |
| `prompts.py` | Contexte système, `DISCLAIMER_TEXT`, `SCOPE_OF_ADVICE_RULES`, modes narratifs |
| `activity_analysis.py` | Feedback post-séance (5 blocs, ton piloté par le TSB) |
| `narrator.py` | Résumé narratif du plan / d'une semaine (texte pur, fallback déterministe) |

Budget de sortie : `settings.llm_max_tokens` (défaut 4000 — certains modèles
OpenRouter consomment beaucoup de tokens de raisonnement cachés avant d'émettre du
contenu visible ; un budget trop bas → `finish_reason=length` → contenu vide →
fallback déterministe silencieux).

### `app/providers/` — accès au monde extérieur

```
app/providers/
├── intervals/            # Source de données unique — intervals.icu
│   ├── client.py         # Auth basic (clé API) ; lecture (get/list) + écriture calendrier
│   ├── errors.py         # Erreurs typées (clé invalide, indisponibilité, quota)
│   ├── mapper.py         # Payload intervals.icu (~183 champs) → AnalyzedSession
│   ├── athlete_profile.py# spec 007 — GET /athlete → ReadProfile (chaque champ porte son origine)
│   ├── history.py        # Import historique à la 1ère connexion (idempotent)
│   ├── poller.py         # Interrogation périodique — détecte les nouvelles activités
│   ├── notifier.py       # Notification post-sortie étagée + clavier RPE
│   ├── wellness.py       # HRV / FC repos / sommeil / CTL / ATL / ramp_rate quotidiens
│   ├── workout_dsl.py    # spec 005 — Step/RepeatGroup → texte DSL intervals.icu + hash de contenu
│   └── calendar.py       # spec 005 — publish_sessions() (diff idempotent), withdraw_event()
│                         #            ne touche JAMAIS la DB (couche provider pure)
└── analysis/             # Logique métier que la source ne fournit pas
    ├── analysis_models.py# AnalyzedSession (DTO commun, provider-agnostic)
    ├── matching.py       # Matching activité ↔ séance planifiée (score 0–100)
    └── highlight.py      # Variable Reward — fait marquant + record personnel
```

**Layering strict** (Constitution Principe III) :
- `providers/intervals/calendar.py` = I/O calendrier pur, **jamais de DB**.
- `services/publication.py` = consentement + diff + persistance, **jamais d'aiogram**.
- `bot/routers/publish.py` = interaction Telegram uniquement.
- `services/guardrail_service.py` = assemblage de findings, **aucun chemin d'écriture**
  (vérifié par scan AST dans les tests).

### `app/db/` — persistance

- `client.py` : `AsyncSessionFactory` (`expire_on_commit=False`).
- `models/` : ORM SQLAlchemy.
- `repositories/` : accès DB — **jamais de SQL dans les handlers**. Fonctions async
  pures (pas de classes), import centralisé via `repositories/__init__.py`.
- `lifecycle.py` : `run_migrations()` — Alembic appliqué automatiquement au démarrage.

### `app/core/`
- `persona.py` : `load_persona()` → `Persona` depuis `personas/*.yaml`
  (câblé spec 007 via `services/coach_voice.py`).
- `exceptions.py` : `BanisterError` + `PersonaNotFoundError`.

---

## 4. Modèle de process (`app/main.py`)

```
lifespan(app):
    run_migrations()                         # Alembic, bloquant, avant tout le reste
    ── dev  :  create_task(dp.start_polling(bot))
    ── prod :  bot.set_webhook(url, secret_token)
    create_task(_run_intervals_poller())     # tick toutes les INTERVALS_POLL_INTERVAL_MINUTES (défaut 5)
    create_task(_weekly_recap_scheduler())   # dimanche 20h
    create_task(_session_reminder_scheduler())# tick 60s, envoie à l'heure choisie par l'athlète
    yield
    ── cleanup : cancel de chaque tâche, fermeture de la session bot
```

Aucune dépendance de scheduling externe (pas d'APScheduler, pas de cron) — de simples
boucles `while True: await asyncio.sleep(...)`.

> **Note dev Windows** : `uvicorn --reload` spawn des process enfants ; un process tué
> laisse un `data/.instance.lock` orphelin et des fichiers `banister.db-wal` /
> `banister.db-shm` qui peuvent bloquer la migration de démarrage. En cas de blocage
> sur « Checking database schema… » : tuer tous les python, supprimer ces trois
> fichiers, relancer. Lancer le bot dans un terminal dédié plutôt qu'en tâche de fond.

---

## 5. Flux de données

### 5.1 Pipeline intervals.icu (lecture — interrogation périodique)

```
poller.py (tick périodique)
   → import_history()   : idempotent, se resume via sync_state.history_import_complete
   → ingest_wellness()  : fenêtre glissante courte, ré-upsert chaque jour (self-healing)
   → client.list_activities() : activités récentes
   → détecte les nouvelles (sync_state) — 1 notification/activité, jamais de doublon
   → mapper.map_activity(payload) → AnalyzedSession
        · CTL/ATL/TSB, TSS, zones, FTP/LTHR : tels quels depuis la source
        · decoupling (%) → cardiac_drift_index (fraction signée ÷100)
        · respect_zones_score, session_type_real : calcul local (la source ignore le plan)
   → matching.evaluate_activity_plan_match(plan, analyzed, date, used_slots)
        · fenêtre ±2j bornée à la semaine d'entraînement
        · score /100 : durée 30 + TSS 30 + type 25 + zone 5 + indoor/outdoor 10
   → notifier.send_staged_notification() : 3 messages (teaser silencieux → métrique
        héros silencieux → verdict + clavier RPE, seule vibration)
   → post-RPE : edit-then-reveal → récit LLM 3 phrases
```

### 5.2 Chat — boucle agentique

```
message texte (PlanStates.ACTIVE)
   → run_chat() : charge profil + plan + logs + historique + guardrail findings
        + MetricRegistry (spec 006)
   → build_system_prompt(persona=resolve_voice(user))   # spec 007
   → run_agentic_loop()  (max 2 itérations)
        ├─ finish_reason == tool_calls → tool_executor(name, args)  [moteur déterministe]
        │     get_upcoming_sessions | update_injury_status | propose_plan_modification
        │     | propose_session_adjustment | update_coach_memory
        └─ sinon → texte de réponse
   → verify_response() → record_check_failure() → apply_result()   # spec 006, retire les
        phrases dont un chiffre ne correspond pas à la registry
   → intent déduit de l'outil appelé ; proposition de modif extraite si applicable
   → si proposition → PlanStates.PENDING_MODIFICATION + boutons [✅ Appliquer] / [❌ Annuler]
```

Le LLM ne peut appeler qu'un seul outil par tour. Le `tool_executor` est une fermeture
sur `session / plan / profile / logs` — c'est lui qui appelle le moteur, jamais le LLM.

### 5.3 Publication vers le calendrier (spec 005 — écriture sortante)

```
/publish
   → request_publication() : horizon = semaine courante + suivante (FR-010),
        liste chaque séance + date + caveat « active le transfert vers ta montre »,
        enregistre un PublicationApproval `pending` lié au content_hash du plan
   ↓ [✅ Publier]
   → authorize_publication()  ← BARRIÈRE UNIQUE, 1ère instruction de execute_publication()
        refuse si hash ≠ (plan changé → StaleApprovalError, FR-004)
   ↓
   → calendar.publish_sessions() : diff idempotent (l'API n'a PAS d'upsert)
        list_events() la fenêtre → garde uniquement les external_id `banister:`
        hash connu == courant + événement présent  → "unchanged"
        présent, contenu différent                 → update_event()
        absent                                     → create_event()
        séance sans steps                          → refusée (FR-009)
   ↓
   · événement modifié/supprimé à la main par l'athlète → "conflict", jamais écrasé (US5)
   · catégorie ≠ WORKOUT (activité réalisée)           → jamais touchée (FR-025)
   · séances passées (date < today)                    → exclues de tous les chemins

/unpublish → withdraw_all_publications() : n'itère que nos propres PublishedEntry
```

**Divergence** : `check_divergence()` — dérivée à la demande, jamais stockée. Si le
plan a évolué depuis la publication, le contexte LLM le signale au coach au lieu de
laisser croire que le calendrier est à jour.

### 5.4 Garde-fous d'entraînement (spec 006)

```
à chaque message de chat + au feedback post-séance :
   assemble_workload_findings(session, user_id)
        · ACWR = wellness.atl / wellness.ctl   (autoritaire, présent les jours de repos)
        · ramp_rate  = gain de CTL/semaine calculé par la source
        · monotonie Foster corrigée (7 jours, jours de repos = 0)
   assemble_recovery_findings(session, user_id)
        · VFC / FC repos vs baselines glissantes PERSONNELLES
        · seuil franchi 2 jours consécutifs, aucun aberrant → sinon rien
        · non évaluable → recovery_insufficiency() renvoie une raison affichée au coach
   → GuardrailFinding (dataclass frozen, `action` obligatoire — inconstructible sans)
   → injectés tels quels dans le system prompt ; le LLM les restitue, n'en produit aucun
   → un refus (GuardrailAcknowledgement, clé kind:jour) démote l'action en rappel
        factuel mais le signal continue d'apparaître ; l'occurrence du lendemain est neuve
```

`guardrail_service` **n'a aucun chemin d'écriture** — une acceptation passe par
`plan_modifier` / `authorize_publication` existants.

---

## 6. Persistance — SQLite local

Fichier unique sous `DATA_DIR` (`data/` par défaut), SQLAlchemy async + `aiosqlite`.
Aucun service à administrer — le fichier est créé et migré au démarrage.

| Table | Contenu |
|-------|---------|
| `users` | compte Telegram, flags onboarding, préférences rappels ; `coach_voice`, `disclaimer_acknowledged_at` (survivent à `/reset`) |
| `athlete_profiles` | `profile` JSON → `AthleteProfileSchema` |
| `training_plans` | `plan_technical` JSON → `TrainingPlanSchema`, `start_date`, `is_active` |
| `session_logs` | séance réalisée : `tss_actual`, `rpe_emoji`, `source_activity_id`, métriques qualité, contexte |
| `chat_messages` | historique LLM (role, content, intent, tool_used) |
| `activities` | import historique (`source="intervals_icu"`, `tss`, `tss_method`, `device_watts`) |
| `weekly_adherence` | taux d'adhérence hebdo — upsert à chaque `/recap` |
| `wellness` | HRV / FC repos / sommeil / CTL / ATL / `ramp_rate` quotidiens — ingérés à chaque tick du poller ; source de `get_current_fitness()` et des signaux de charge |
| `response_check_failures` | spec 006 — 1 ligne par chiffre d'une réponse LLM qui ne correspond pas ; jamais purgée (SC-001/002 sont des mesures) |
| `guardrail_acknowledgements` | spec 006 — décision de l'athlète sur une occurrence (`occurrence_key = kind:jour`) |
| `publication_approvals` | spec 005 — consentement lié au contenu (`content_hash` SHA-256) ; `status` terminal |
| `published_entries` | spec 005 — 1 ligne par séance écrite ; `external_id` unique, `intervals_event_id`, `withdrawn_at` |

`oauth_connections` a été supprimée (spec 002) — l'auth intervals.icu est une clé API
personnelle, pas un flux OAuth.

**Migrations** : Alembic (`migrations/versions/`), baseline `ecd6f700779f`. Appliquées
automatiquement au démarrage — jamais à la main. Nouveau champ → `alembic revision
--autogenerate`, jamais un `.sql` écrit à la main.

> ⚠️ Connu et accepté : une migration Alembic qui échoue en cours de route sur SQLite
> ne s'annule pas automatiquement (contrairement à PostgreSQL). En mono-utilisateur
> avec `scripts/backup.py` disponible, ce risque est accepté tel quel.

**Piège SQLAlchemy JSON** — les mutations de champs JSON ne sont pas auto-détectées :
```python
plan.plan_technical = new_data
flag_modified(plan, "plan_technical")
await session.flush()
```

---

## 7. Frontières de scope connues (pas des oublis)

| Sujet | État |
|-------|------|
| `fit_template()` (spec 004) | Construit et testé (`tests/test_engine/test_fitting.py`), **pas branché à `generate_plan()`**. `_build_sessions()` utilise encore sa propre arithmétique de budget TSS, délibérément conservée pour ne pas perturber la logique de placement physiologique de `_assign_sessions_to_days()`. Le brancher est un travail à part entière (mérite sa propre spec). |
| Écriture-retour d'une correction FTP vers intervals.icu (spec 007 FR-006) | Différée derrière une sonde d'endpoint à autoriser séparément (mute les réglages du compte). Défaut livré = FR-007 : l'athlète corrige sur intervals.icu, aucune valeur locale divergente. |
| `personas` × modes narratifs | Deux axes **séparés**, non fusionnés : une persona est *qui est le coach* (`personas/*.yaml`, `/voice`) ; un mode narratif est *comment une sortie est racontée* (`prompts.py::build_narrative_system_prompt`). |

---

## 8. Variables d'environnement

```
DATA_DIR=data                            # optionnel — dossier de données (SQLite)
DATABASE_URL=                            # optionnel — override, sinon SQLite dérivé de DATA_DIR
TELEGRAM_BOT_TOKEN=...
TELEGRAM_OWNER_ID=...                    # user ID Telegram du propriétaire (garde mono-utilisateur)
TELEGRAM_WEBHOOK_URL=https://<domaine>/webhook/telegram   # prod uniquement
TELEGRAM_WEBHOOK_SECRET=...
INTERVALS_API_KEY=...                    # clé API personnelle intervals.icu (basic auth)
# INTERVALS_ATHLETE_ID=0                 # optionnel — "0" = l'athlète propriétaire de la clé
INTERVALS_POLL_INTERVAL_MINUTES=5        # optionnel — défaut 5
LLM_PROVIDER=openrouter                  # ou "anthropic"
LLM_MODEL=...
CHAT_MODEL=...
LLM_MAX_TOKENS=4000                      # optionnel — budget de sortie des appels rédactionnels
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...                    # si LLM_PROVIDER=anthropic
LOG_LEVEL=INFO
```

Démarrage dev : `python -m uvicorn app.main:app --port 8000 --reload`

---

## 9. Tests

```
pytest tests/                     # suite complète
pytest tests/test_engine/         # moteur déterministe (pur, rapide)
pytest tests/test_providers/      # client / mapper / poller / notifier / calendar intervals.icu
pytest tests/test_analysis/       # matching activité ↔ plan
pytest tests/test_services/       # orchestration (publication, guardrails, recap)
pytest tests/test_bot/            # flux FSM (setup, goal, reset) sur SQLite in-memory
ruff check app/ tests/
```

Les scénarios de bout en bout vivent dans `specs/NNN-*/quickstart.md` et se lancent
via les scripts de `scripts/` (`calendar_state.py`, `publish_horizon.py`,
`guardrail_state.py`, `verify_corpus.py`, `athlete_profile.py`).
