# Banister — Guide Claude

## Vue d'ensemble

Bot Telegram de coaching cyclisme IA. Génère et adapte des plans d'entraînement personnalisés.
Architecture en un seul process : aiogram v3 (bot) + FastAPI (webhooks/OAuth) + moteur déterministe Python + LLM.

**Principe fondateur : le LLM ne calcule jamais la charge d'entraînement — moteur déterministe uniquement.**

## Refonte open source — terminée (specs 001–007)

Ce document décrit **l'état actuel du code**. La refonte vers un produit self-hosted open source
(specs `001`–`007`, gouvernée par `.specify/memory/constitution.md` **v1.1.0**) est **complète**.

**✅ Fait** : spec 003 (SQLite local remplace Supabase), spec 002 (intervals.icu remplace Strava, log manuel
supprimé), spec 004 (séances structurées, bibliothèque de templates, fitting), spec 005 (push des séances
vers le calendrier intervals.icu — `/publish`, `/unpublish`), spec 006 (garde-fous d'entraînement +
vérification des chiffres de la réponse LLM), spec 007 (setup = confirmation de ce que la source sait ;
`/goal`, `/reset`, `/voice` ; `load_persona()` câblé).

Reste hors specs, connu :
- `docs/ARCHITECTURE.md` réécrit (2026-09-07) pour refléter l'état specs 001–007 — modèle de process,
  couches, flux de données ; complémentaire de ce fichier (qui garde l'historique spec par spec et la
  navigation). `CLAUDE.md` reste la référence vivante (constitution v1.1.0).
- Écriture-retour d'une correction FTP vers intervals.icu (spec 007 FR-006) : différée derrière une sonde
  d'endpoint autorisée séparément. Défaut livré = FR-007 (l'athlète change sur intervals.icu, aucune
  valeur locale divergente).
- `fit_template()` (spec 004) toujours pas branché à `generate_plan()` — le brancher mérite sa propre spec
  (ne pas perturber le placement physiologique de `_assign_sessions_to_days()`). Il est en revanche
  maintenant utilisé en production côté mode libre (spec 009, `app/engine/freestyle_selector.py`), donc
  n'est plus « construit et testé mais jamais appelé » — juste pas câblé à la génération de plan.

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
│   ├── persona.py           # load_persona() → Persona depuis personas/*.yaml (câblé spec 007
│   │                        # via services/coach_voice.py — /voice choisit users.coach_voice)
│   └── exceptions.py        # BanisterError + PersonaNotFoundError
├── bot/
│   ├── setup.py             # Dispatcher + middlewares + routers (ordre critique)
│   ├── states.py            # FSM : SetupStates, PlanStates (SessionLogStates supprimé — spec 002)
│   ├── middlewares/
│   │   ├── db_session.py    # Ouvre AsyncSession (doit précéder single_user)
│   │   └── single_user.py   # Garde TELEGRAM_OWNER_ID + injection User (pas d'upsert)
│   ├── keyboards/           # Prefixes callbacks : setup: / plan: / log: / chat: / rem: / pub: / goal: / voice:
│   ├── routers/             # Ordre réel dans setup.py : common → setup → plan → session_log →
│   │                        # forme → recap → reminders → publish → goal → reset → voice → chat (DERNIER)
│   │   └── publish.py       # /publish (approbation + écriture calendrier), /unpublish (spec 005)
│   └── (chat doit rester en dernier — catch-all)
├── db/
│   ├── client.py            # AsyncSessionFactory (expire_on_commit=False)
│   ├── models/              # ORM SQLAlchemy
│   └── repositories/        # Accès DB — jamais de SQL dans les handlers
│       ├── weekly_adherence_repo.py  # upsert + get_recent — persistance taux d'adhérence /recap
│       ├── publication_repo.py       # PublicationApproval + PublishedEntry (spec 005)
│       ├── guardrail_repo.py         # ResponseCheckFailure + GuardrailAcknowledgement (spec 006)
│       └── meal_entry_repo.py        # create/delete_for_date/daily_totals — suivi calorique (spec 008)
├── services/                # Orchestration : repos + LLM, sans dépendance aiogram
│   ├── weekly_recap.py      # compute_weekly_recap() → WeeklyRecapResult
│   ├── activity_feedback.py # assemble_activity_feedback() — contexte post-séance, sans dépendance bot
│   ├── fitness.py           # get_current_fitness() — CTL/ATL/TSB courants depuis la table wellness
│   ├── publication.py       # spec 005 : cycle de vie approbation, barrière authorize_publication(),
│   │                        # diff plan↔calendrier, check_divergence(), retrait — sans dépendance aiogram
│   ├── guardrail_service.py # spec 006 : assemble_workload/recovery_findings, recovery_insufficiency,
│   │                        # décline/accepte via chemins existants — AUCUN chemin d'écriture propre
│   ├── response_verification.py  # spec 006 : MetricRegistry + verify_response + apply_result (US3)
│   ├── nutrition_reminder.py # spec 008 : needs_reminder() — pur, testable sans importer app/main.py
│   └── coaching_mode.py     # spec 009 : get_coaching_mode()/mode_from_plan() — mode dérivé de
│                            # l'existence d'un plan actif, jamais stocké
├── engine/                  # Moteur déterministe — zéro LLM ici
│   ├── schemas.py           # Pydantic : AthleteProfileSchema, TrainingPlanSchema, SessionSpec, Step, RepeatGroup
│   ├── periodization.py     # Blocs Base/Build/Peak/Taper
│   ├── plan_builder.py      # Génération plan complet — sélectionne depuis session_library.py
│   ├── plan_modifier.py     # Modification plan (outil LLM) — préserve/adapte les steps
│   ├── session_library.py   # Charge/valide sessions/*.yaml, sélection déterministe (spec 004)
│   ├── fitting.py           # Adapte un template à une cible de charge (spec 004, pas encore branché à
│   │                        # generate_plan() — mais utilisé par freestyle_selector.py, spec 009)
│   ├── freestyle_selector.py # spec 009 : choose_workout_type()/build_freestyle_suggestion() — remplace
│   │                        # la phase de périodisation par l'état de forme comme entrée de sélection
│   ├── session_render.py    # Description dérivée des steps, paramétrée par langue (spec 004)
│   ├── atl_ctl.py           # ATL/CTL/TSB (EMA τ=7j/42j) — calculé localement, la source ne le fournit pas
│   ├── adherence_kpi.py     # Score KPI par séance (0–2.0 pts) + bloc KPI hebdo
│   ├── weekly_snapshot.py   # WeeklySnapshot : tendance charge, monotonie Foster (corrigée spec 006)
│   ├── guardrails.py        # spec 006 : évaluateurs purs (ACWR, ramp, monotonie, VFC, FC repos) + GuardrailFinding
│   ├── baselines.py         # spec 006 : baselines glissantes personnelles (rolling_baseline*)
│   └── guardrail_thresholds.py  # spec 006 : tous les seuils + leur source publiée (FR-016, SC-007)
├── llm/
│   ├── factory.py           # Sélection provider (openrouter | anthropic)
│   ├── providers/           # anthropic.py, openrouter.py — interface commune generate()
│   ├── chat_client.py       # run_agentic_loop() — max 2 itérations outils
│   ├── chat.py              # run_chat() → (text, intent, tool_used, pending_proposal)
│   ├── tools.py             # 9 outils LLM + build_system_prompt() + tools_for_mode() (spec 009)
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

## Garde-fous d'entraînement (spec 006)

Le coach signale quand l'athlète va vers un mur — charge qui monte trop vite, entraînement sans
récupération, semaine monotone — et **vérifie que les chiffres qu'il énonce sont vrais**.

**Tout est déterministe** (`app/engine/guardrails.py`, `baselines.py`, `guardrail_thresholds.py`) : le
LLM restitue des `GuardrailFinding` qu'on lui donne, il n'en produit aucun (FR-022). Un `GuardrailFinding`
dont l'`action` est vide est **inconstructible** (SC-003).

**Signaux de charge** (`assemble_workload_findings`) :
- ratio aigu/chronique = `ATL / CTL` **lu depuis la table `wellness`** (autoritaire, dédupliqué, présent les
  jours de repos — pas recalculé depuis une série locale qui double-compte, R3). Se déclenche uniquement
  au-dessus de la plage ; un taper (ratio bas) n'est jamais signalé comme désentraînement.
- `ramp_rate` = gain de CTL/semaine calculé par la source (R4), signal indépendant.
- monotonie Foster **corrigée** (voir Snapshot hebdo ci-dessous).
- si un signal exige de baisser la charge → `GUARDRAIL_LOAD_REDUCTION_RULE` ajoutée au system prompt : la
  réponse ne peut plus recommander d'augmenter la charge (FR-003).

**Signaux de récupération** (`assemble_recovery_findings`) — VFC et FC de repos seulement (pas de seuil
publié pour le sommeil) :
- baselines glissantes **personnelles** (`baselines.rolling_baseline*`), `None` sous `BASELINE_MIN_SAMPLES`.
- une finding exige le seuil franchi **2 jours consécutifs**, aucun aberrant (`is_anomalous_reading`,
  `sustained_recovery_finding`) — un seul mauvais jour ou un glitch capteur ne déclenche jamais seul
  (FR-011).
- ≥2 signaux bas → une seule finding `recovery_multi` de sévérité haute (FR-009).
- conflit avec une séance dure prévue → énoncé ouvertement (`state_conflict_with_plan`, FR-012).
- si non évaluable → `recovery_insufficiency()` renvoie une raison (aucun historique / baseline périmée /
  baseline courante sans mesure du jour), affichée au coach pour que le silence ne passe pas pour « récup
  OK » (FR-014). ⚠️ état réel du compte : plus aucune mesure VFC/FC repos depuis juillet 2025 → cette
  branche est le chemin réellement exercé.

**`recovery_index`** (2026-09-21, chantier "signaux enrichis intervals.icu", inspiré de Section11) :
`(HRV_jour/HRV_baseline_7j) / (RHR_jour/RHR_baseline_7j)` — signal composite distinct de `hrv_low`/
`rhr_high` (qui évaluent chaque métrique séparément) : capture le cas où VFC et FC de repos divergent
toutes les deux dans le mauvais sens sans qu'aucune seule ne franchisse son propre seuil. Baseline
**7 jours** (`RECOVERY_INDEX_BASELINE_WINDOW_DAYS`), volontairement distincte des 28 jours de
`HRV_DROP_PCT`/`RHR_RISE_BPM`. Déclenche le jour même, sans exigence de 2 jours consécutifs (déjà construit
sur deux moyennes lissées). `RECOVERY_INDEX_LOW = 0.90` **n'est pas une valeur de la littérature publiée**
— aucune source académique identifiée pour ce ratio précis, documenté comme jugement (même statut que
`ACWR_MIN_CTL`). Alimente aussi le `MetricRegistry` (avec `hrv`/`rhr` bruts — gap pré-existant comblé au
passage : les regex d'ancrage existaient déjà sans que rien ne les alimente) et le bloc FORME ACTUELLE du
prompt du chat (`app/llm/tools.py::build_system_prompt()` uniquement — pas `/review`/`/recap`).

**Advisory, jamais autoritaire** : `guardrail_service` **n'a aucun chemin d'écriture** (vérifié par scan
AST dans les tests). Une acceptation passe par `plan_modifier` / `authorize_publication` existants. Un
refus est enregistré (`GuardrailAcknowledgement`, clé `kind:jour`) → l'`action` est démotée en simple
rappel factuel mais le signal continue d'apparaître (FR-025/FR-026) ; l'occurrence du lendemain est neuve.

**Vérification des réponses** (`app/services/response_verification.py`, US3) :
- `MetricRegistry` = `{nom: valeur}` assemblé dans `chat.py` — la définition de « retrouvé » (FR-018).
- `verify_response()` : **ancrage sur mots-clés** — un nombre est une affirmation seulement s'il est dans
  la même clause qu'un terme métrique (CTL, ATL, TSB, ratio, VFC…). Durées, zones, `%` relatifs → jamais
  des affirmations (R5). Classe `pass` / `mismatch` / `unretrieved` avec tolérance (arrondi d'affichage OK).
- `apply_result()` : retire **la phrase** portant une affirmation fautive, jamais toute la réponse, ne
  réécrit jamais autour d'un nombre corrigé (R6).
- Échecs enregistrés (`response_check_failures`, gardés indéfiniment — SC-001/SC-002 sont des mesures).
- `chat.py` : `verify → record → apply` après `run_agentic_loop`, avant de renvoyer.

**Disclaimer** : `prompts.DISCLAIMER_TEXT` (« ni médecin ni coach certifié, les séances sont des
suggestions ») émis en fin de `/setup` + README (FR-028). `prompts.SCOPE_OF_ADVICE_RULES` (renvoi médecin
si signaux d'infection, jamais de diagnostic sur une douleur) dans `build_ux_system_prompt`. Spec 007
relocalisera le disclaimer au flux first-run.

**Seuils** : `app/engine/guardrail_thresholds.py` — chaque seuil avec sa source publiée, lu comme de la
doc (FR-016, SC-007). ⚠️ le ratio `ATL/CTL` est du 7j:42j (EWMA), la plage 0.8–1.3 de Gabbett était du
7:28 (rolling) — provenance signalée dans le module (Williams et al. 2017).

**Scripts** : `scripts/guardrail_state.py --describe` (état des signaux par athlète) ;
`scripts/verify_corpus.py --last N` (revue manuelle du taux de faux positifs du vérificateur).

## Tables SQLite

| Table | Description |
|-------|-------------|
| `users` | Compte Telegram, flags onboarding (`onboarding_completed_at`), préférences rappels (`reminders_enabled`, `reminder_hour`, `reminder_minute`, `reminder_last_sent_at`) ; spec 007 : `coach_voice` (id persona choisi, NULL → `settings.persona`), `disclaimer_acknowledged_at` (disclaimer montré 1×). Ces deux-là survivent à `/reset` |
| `athlete_profiles` | `profile` JSON → `AthleteProfileSchema` |
| `training_plans` | `plan_technical` JSON → `TrainingPlanSchema`, `start_date`, `is_active` |
| `session_logs` | `plan_id`/`week_number`/`day_of_week` **nullable depuis spec 009** (`NULL` = séance loggée en mode libre, sans plan — `status="unplanned"` par construction) ; `tss_actual`, `rpe_emoji`, `logged_date`, `source_activity_id` (id intervals.icu) ; métriques qualité (`cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`, `session_type_real`, `variability_index`, `intensity_factor`, `dominant_zone`) ; contexte (`elevation_gain_m`, `average_temp_c`, `athlete_count`) |
| `chat_messages` | Historique LLM (role, content, intent, tool_used) |
| `activities` | Import historique (`source="intervals_icu"`, `source_activity_id`, `tss`, `tss_method`, `device_watts`) |
| `weekly_adherence` | Taux d'adhérence hebdomadaire — upsert à chaque `/recap` ; clé `(user_id, week_start_date)` ; colonnes : `sessions_done`, `sessions_planned`, `compliance_pct`, `tss_7d`, `week_number`, `plan_id` |
| `wellness` | HRV / FC repos / sommeil / CTL / ATL / **`ramp_rate`** quotidiens — ingérée à chaque tick du poller (`ingest_wellness`), source de `get_current_fitness()` et des signaux de charge (spec 006). `ramp_rate` = gain de CTL/semaine calculé par la source, consommé tel quel. Depuis 2026-09-21 (chantier "signaux enrichis intervals.icu", inspiré de Section11) : ~31 champs bruts /wellness supplémentaires, tous consommés tels quels — `hrv_sdnn`, `sleep_quality`/`sleep_score`, `mental_energy`, `avg_sleeping_hr`, `vo2max`, `fatigue`/`soreness`/`stress`/`mood`/`motivation`/`injury`/`hydration` (échelle 1-4, 1=meilleur état), `spo2`, `blood_glucose`, `systolic`/`diastolic`, `baevsky_si`, `lactate`, `respiration`, `body_fat_pct`, `abdomen_cm`, `steps`, `hydration_volume_l`, `kcal_consumed`, `carbohydrates_g`/`protein_g`/`fat_g`, `menstrual_phase`/`menstrual_phase_predicted`, `readiness`. `sleep_quality`/`sleep_score`/`fatigue`/`stress`/`mood`/`motivation` rejoignent le bloc FORME ACTUELLE de `build_system_prompt()` (le chat uniquement — pas `/review`/`/recap`) ; le reste (macros, spO2, tension, glycémie, lactate, menstrual_phase...) reste stocké mais non surfacé, en attente d'un besoin réel (leçon : HRV/RHR eux-mêmes vides depuis juillet 2025 sur le compte de test) |
| `response_check_failures` | spec 006 — une ligne par chiffre d'une réponse LLM qui ne correspond pas à ce qui a été retrouvé (`failure_kind` mismatch/unretrieved, `stated_value`, `expected_value`, `response_excerpt`). Jamais purgée : SC-001/SC-002 sont des mesures sur un corpus |
| `guardrail_acknowledgements` | spec 006 — décision de l'athlète sur une occurrence de garde-fou (`occurrence_key` = `kind:jour`, `decision` accepted/declined). Un refus démote l'action sans museler le signal (FR-025/FR-026) |
| `publication_approvals` | spec 005 — consentement enregistré et lié au contenu (`content_hash` SHA-256 sur ce qui a été montré) ; `status` pending/approved/declined (terminal, jamais supprimé — FR-003) ; `horizon_start`/`horizon_end`, `session_count` |
| `published_entries` | spec 005 — une ligne par séance écrite au calendrier ; `external_id` unique/user (`banister:<plan>:<date>:<slug>`), `intervals_event_id`, `approval_id` (FR-005), `content_hash`, `withdrawn_at` (gardée en historique — distingue « retirée par nous » de « supprimée par l'athlète ») |
| `meal_entries` | spec 008 — un repas ou un récap de journée loggé en langage naturel via le chat ; `entry_date`, `entry_type` (meal/day_recap), `meal_slot` (nullable), `raw_description` (texte verbatim de l'athlète), `estimated_calories` (estimation LLM, jamais recalculée). Gardée indéfiniment, **jamais purgée par `/reset`** (ni donnée d'entraînement, ni identité — un historique perso que l'athlète a explicitement demandé de garder) |

`oauth_connections` a été supprimée (spec 002 T059) — l'authentification intervals.icu est une clé API
personnelle, pas un flux OAuth, donc aucune table de tokens n'est nécessaire.

Migrations : Alembic (`migrations/versions/`), appliquées automatiquement au démarrage
(`app/db/lifecycle.py::run_migrations()`) — jamais à la main. `init.sql` a été supprimé (spec 003 T050) ;
la baseline Alembic (`ecd6f700779f`) le remplace intégralement. Nouveau champ DB → `alembic revision
--autogenerate`, jamais un fichier SQL écrit à la main.

Une migration Alembic qui échoue en cours de route sur SQLite **ne s'annule pas** automatiquement
(contrairement à PostgreSQL) — SQLite ne supporte pas les DDL transactionnelles de la même façon. Depuis
2026-09-18, ce risque n'est plus seulement "accepté" : `app/services/backup.py::run_startup_backup()` prend
un snapshot (`VACUUM INTO`, même mécanisme que `scripts/backup.py`) juste avant `run_migrations()` dans
`lifespan` (`app/main.py`), donc une migration cassée reste récupérable via le backup pris quelques secondes
plus tôt. Auto-géré : dossier `data/backups/`, garde les 7 derniers, ignore les backends non-SQLite (URL
`DATABASE_URL` explicite), et un throttle de 20h évite l'accumulation lors des redémarrages répétés
(`--reload` en dev). Ne bloque jamais le démarrage — une erreur de backup est loggée puis ignorée, puisque
le risque qu'elle protège ne s'est pas encore matérialisé.

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

### Semaine de course (`app/engine/plan_builder.py::_build_race_week`)
Le marqueur "jour de course" (`day_of_week=race_dow`, la vraie date de l'événement) est **volontairement**
en dehors de `preferred_days` — on ne déplace pas une course pour coller aux jours d'entraînement préférés.
Toute règle/test qui vérifie "toutes les séances sont dans `preferred_days`" doit exclure ce marqueur
(reconnaissable à `"JOUR DE COURSE"` dans `description_fr`) — trouvé le 2026-09-18 après qu'un test l'ait
raté et fait un faux positif de bug moteur (le test échouait ~3 jours sur 7 selon le jour d'exécution,
`tests/test_engine/test_plan_builder.py::test_sessions_on_available_days_only`, jamais figé sur une date).

⚠️ Connu et pas corrigé (trouvé le 2026-09-18, même investigation) : quand `target_date` tombe exactement
sur un lundi (jour de début de semaine), `weeks_total = (target_date - week_start).days // 7` sous-compte
d'une semaine — la dernière semaine générée s'arrête la veille du lundi de la course au lieu de l'inclure,
donc `_build_race_week` ne se déclenche jamais et le marqueur de course disparaît silencieusement. Rare
(1 date cible sur 7 selon le jour de la semaine), pas encore scopé en fix — touche le calcul de
`weeks_total` dans `generate_plan()`, zone à traiter avec soin (periodization/phases en dépendent).

### Telegram / parse_mode
- `parse_mode="HTML"` partout où les messages contiennent des underscores (identifiants, chemins)
- `parse_mode="Markdown"` uniquement si le texte est garanti sans underscore hors italique

### FSM aiogram
- `MemoryStorage` → états perdus au redémarrage — `UserLoaderMiddleware` restaure `PlanStates.ACTIVE` depuis la DB
- `FSMContext` inaccessible dans les middlewares (injecté après par aiogram)
- `chat_router` doit être enregistré en DERNIER dans `setup.py` (catch-all)

### Autorité de la source (spec 002, Constitution Principe IV)
- **Consommés tels quels, jamais recalculés** : charge d'entraînement de l'activité, CTL/ATL/TSB, zones
  puissance/FC, seuils FTP/LTHR, `efficiency_factor`/`hrr` (HRRc)
  - Par activité : `app/providers/intervals/mapper.py` (TSS, zones, seuils, `efficiency_factor`=
    `icu_efficiency_factor`, `hrr`=`icu_hrr.hrr` — **`icu_hrr` est un objet**
    `{start_bpm, end_bpm, hrr, ...}`, pas un scalaire, seul le champ `hrr` nous intéresse — vérifié sur
    fixture réelle avant d'écrire le mapping, 2026-09-21, chantier "signaux enrichis intervals.icu")
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
- **Monotonie Foster** : `mean(TSS journaliers) / std` **sur les 7 jours de la fenêtre, jours de repos à 0**
  — score > `MONOTONY_HIGH` (2.0) = charge monotone = risque ; `None` si aucun entraînement dans la fenêtre
  ou 7 jours identiques (std=0). ⚠️ **corrigé spec 006 R2** : l'ancienne version jetait les jours de repos
  et sur-signalait (indice ~2.6 sur une semaine en réalité très variée). 3 call sites migrés vers la
  constante `MONOTONY_HIGH` (`weekly_recap.py` ×2, `activity_analysis.py`) — comportement utilisateur
  changé, pas une simple refacto
- Fonction pure, pas de requête DB — prend la liste `logs` déjà en mémoire

### TID / indice de polarisation (`app/engine/tid.py`, 2026-09-21, chantier "signaux enrichis intervals.icu")
- `compute_tid(logs, today, window_days)` → `TIDResult | None` — `None` si aucune séance avec zone-time
  dans la fenêtre (jamais un TID à 0/0/0 halluciné sur zéro donnée)
- **Scope volontairement limité aux `SessionLog`** (pas `Activity`, qui n'a aucune colonne de temps par
  zone) — donc seulement les séances effectivement loguées via poller/chat, pas tout l'historique brut
- 3 zones (Seiler 2010) mappées depuis les 7 zones de puissance intervals.icu, même regroupement que
  `mapper.py::_detect_session_type` : zone1 (faible) = Z1+Z2, zone2 (modérée) = Z3+Z4, zone3 (élevée) =
  Z5+Z6+Z7 — "SS" exclu (même précédent que `_dominant_zone`)
- **Indice de polarisation** (Treff et al. 2019, *"The Polarization-Index..."*, Front Physiol) :
  `log10((zone1_pct/zone2_pct) × zone3_pct)` — ⚠️ **le `×100` de la formule publiée ne doit PAS être
  réappliqué** quand les zones sont déjà en pourcentage (0-100), pas en fraction (0-1) : un bug exactement
  de ce type (× 100 en trop, gonflant l'indice de ~2 ordres de grandeur, rendant "polarized" quasi
  inévitable même sur une semaine dominée par la zone 2) a été trouvé et corrigé **en écrivant les tests**,
  avant tout usage réel — voir `tests/test_engine/test_tid.py`. Seuil `POLARIZATION_INDEX_THRESHOLD = 2.0`
  = seule frontière numérique du module validée par une source publiée (rameurs olympiques)
- Classification (base/polarized/pyramidal/threshold/high_intensity) : le reste de l'arbre est une
  **adaptation** des catégories descriptives de Seiler, pas une reprise littérale de seuils publiés —
  documenté comme tel dans `guardrail_thresholds.py`, bloc "TID / Polarisation"
- Surfaces : `build_review_user_message()` (`/review`) et `WEEKLY_RECAP_COACH_TEMPLATE` (`/recap`) — deux
  insertions séparées, chacune dans sa fonction de prompt jetable ; **pas** dans `build_system_prompt()`
  (le chat) pour l'instant

### Phase diagnostique — `phase_detection` (`app/engine/phase_detection.py`, 2026-09-21, porté de Section11)
- **Distincte de la phase prescriptive** `app/engine/periodization.py`/`week.phase` (qui construit le plan
  au moment de sa génération). Celle-ci est **diagnostique** : inférée du comportement récent réel. Même
  vocabulaire FR (`narrator.PHASE_FR`, réutilisé tel quel) mais jamais nommé `phase` — toujours
  `detected_phase`, pour ne jamais entrer en collision là où les deux pourraient apparaître ensemble
- **Port simplifié, pas une parité ligne à ligne** : le script source (Section11, fourni par l'utilisateur)
  fait ~700 lignes de règles à deux flux. Cette v1 garde un flux principal obligatoire (comportemental,
  `_primary_phase()` — tendance de charge 7j vs moyenne 6 semaines + nombre de jours durs, réutilise
  `compute_weekly_snapshot()`) et un flux secondaire best-effort (phase du plan actif si disponible, sinon
  proximité de la date cible de l'objectif) — recoupement noté (`streams_agree`), jamais caché
- Seuils du flux principal (`_TAPER_LOAD_DROP_PCT`, `_BUILD_LOAD_RISE_PCT`, `_PEAK_MIN_HARD_DAYS_7D`) et du
  flux secondaire (bandes de jours avant la date cible) : **adaptation propre au module, aucune source
  publiée identifiée** pour ces bornes précises — documentés comme jugement dans le module lui-même
- `detect_training_phase(logs, today, plan_week_phase=None, target_date=None)` → `PhaseDetectionResult |
  None` — `None` si `logs` est vide, jamais une phase par défaut hallucinée
- Coexiste sans changement avec `app/engine/freestyle_selector.py` (portée différente : choix d'une séance
  unique vs classification macro/hebdomadaire) — pas d'intégration entre les deux dans ce chantier
- Surface : **prompt du chat uniquement** (`app/llm/tools.py::build_system_prompt()`), bloc positionné
  avant les signaux journaliers (`recovery_index`, wellness qualitatif) — volatilité hebdomadaire, pas
  quotidienne. `/review` et `/recap` ne sont pas concernés

### Power-curve delta / sustainability_profile (`app/engine/power_curve.py`, 2026-09-21, porté de Section11)
- **Endpoint vérifié contre l'API réelle** (pas seulement contre le script source) le jour de l'implémentation
  — `client.get_power_curves(curve_type="power"|"hr", windows=[(oldest,newest),...], activity_type=None)` →
  `GET /athlete/{id}/power-curves` ou `/hr-curves`, params `type` (cyclisme uniquement) + `curves=
  r.<oldest>.<newest>,...` (plusieurs fenêtres en un seul appel). Réponse `{"list": [{"id": "r.<oldest>.
  <newest>", "secs": [...], "watts": [...], ...}], "activities": [...]}` — courbes retrouvées par `id`,
  jamais par position (une fenêtre sans activité qualifiante est simplement omise)
- `compute_power_curve_delta()` : 5 ancrages (5s/60s/300s/1200s/3600s) sur deux fenêtres 28j, `rotation_index`
  = moyenne(deltas courts 5s/60s) − moyenne(deltas longs 1200s/3600s), 300s exclu. Garde-fou : minimum 3
  ancrages valides par fenêtre, sinon `note` explicite plutôt qu'un delta halluciné sur données clairsemées
- `compute_sustainability_profile()` (cyclisme uniquement — Banister ne suit pas ski/rowing) : fusionne le
  meilleur watt par ancrage entre types d'activité (Ride + VirtualRide, l'appelant fait un fetch par type et
  passe la liste des réponses) sur une fenêtre unique de 42j, comparé à deux modèles fermés — Coggan
  (`COGGAN_DURATION_FACTORS`, Allen & Coggan 3e éd., facteurs de FTP par durée 300s-7200s) et Skiba CP/W'
  (`P = CP + W'/t`, CP approximé par la FTP, W' lu depuis `power_model.w_prime` de l'athlète) — aucun fitting,
  formules fermées uniquement. `model_divergence_pct = (réel − modèle_CP) / modèle_CP × 100`
- Calcul **à la demande**, pas de nouvelle colonne wellness/session_log ni d'appel à chaque tick du poller
  (ce n'est pas une donnée journalière) — décision : éviter un appel API de plus par jour pour un signal
  utile ponctuellement
- **Câblé dans `/forme`** (2026-09-21) — `app/bot/routers/forme.py::_fetch_power_profile()` : 3 appels
  power-curves (delta 2×28j + sustainability Ride/VirtualRide 42j) + 1 appel wellness du jour pour W'
  (`sportInfo[].wPrime`, pas encore stocké localement — lu à chaque appel, pas persisté). Best-effort,
  message Telegram séparé, silencieux (`None`) si pas de FTP déclaré, endpoint en échec, ou pas assez de
  données — jamais affiché comme si la donnée existait. `_format_power_profile()` = texte HTML déterministe
  (pas de LLM), volontairement pas passé au `MetricRegistry` (décision owner 2026-09-21 : même statut que
  EF/HRR/TID côté vérification — pas de garde-fou post-hoc sur `/forme`/`/review`, cohérent avec l'existant)

### DFA α1 (`app/engine/dfa.py`, 2026-09-21, porté de Section11 — AlphaHRV)
- **Consomme, ne recalcule pas** (Principe IV) : AlphaHRV (champ Garmin Connect IQ) calcule déjà l'exposant
  DFA α1 sur la montre ; le stream `dfa_a1` remonte pré-calculé via `client.get_activity_streams()`. Ce
  module filtre le bruit et détecte des franchissements de seuil sur la série déjà calculée — aucune
  ré-analyse DFA depuis des intervalles RR bruts
- 🐛 **Bug pré-existant trouvé et corrigé au passage** : `get_activity_streams()` faisait `assert
  isinstance(result, dict)` — la vraie réponse de `GET /activity/{id}/streams` est une **liste**
  `[{"type":..., "data":...}, ...]`, jamais un dict. Cette méthode existait depuis un moment mais n'avait
  jamais été appelée nulle part dans l'app — l'assert n'avait donc jamais été exercé en conditions réelles
  avant cette vérification. Normalisation liste→dict extraite dans
  `app/providers/intervals/streams.py::streams_to_dict()`
- `compute_dfa_block(streams)` → `DFABlock | None` — `None` si le stream `dfa_a1` est absent (pas
  d'enregistrement AlphaHRV sur cette activité), distinct d'un bloc présent avec `quality.sufficient=False`
  (AlphaHRV a tourné mais données inutilisables — trop court ou trop bruité)
- Filtres (ordre) : rejette les zéros-sentinelles AlphaHRV (`dfa_a1 < 0.01`), puis les secondes où
  `artifacts% > 5` (convention Altini). `DFA_LT1 = 1.0` / `DFA_LT2 = 0.5` (validés cyclisme — Rowlands et al.
  2017, Gronwald 2020, Mateo-March et al. 2023, cités dans le script source) : franchissements de bande
  (±0.05, dwell minimum 60s), TIZ à 4 bandes, dérive premier/dernier tiers (interprétable seulement si
  <15% du temps supra-LT2 — sinon c'est de l'intervalle, pas du steady-state)
- ⚠️ **Aucune activité AlphaHRV réelle sur le compte de test au moment de l'implémentation** — vérifié
  2026-09-21 : `dfa_a1` absent des streams disponibles sur la dernière sortie. L'algorithme est porté
  fidèlement et testé sur des séries synthétiques (`tests/test_engine/test_dfa.py`), la plomberie
  (`get_activity_streams`, confirmée fonctionnelle contre l'API réelle) est prête, mais **rien n'est encore
  validé contre une vraie lecture AlphaHRV** — à revisiter dès la première activité réelle enregistrée
- **Câblé dans `/review`** (2026-09-21) — `app/bot/routers/review.py::_fetch_dfa()` : un appel réseau
  best-effort de plus, séparé du reste de `/review` (DB uniquement), déclenché seulement si
  `log.source_activity_id` existe. `None` sans crash si pas d'activité source, si l'appel échoue, ou si
  `dfa_a1` est absent des streams (aucun AlphaHRV — le cas normal pour la plupart des comptes). Affiché dans
  `build_review_user_message()` seulement si `quality.sufficient=True` (un bloc insuffisant reste muet,
  pas de bruit dans le prompt pour une donnée inexploitable). Même décision que power-curve : pas de
  `MetricRegistry`/vérification post-hoc sur cette valeur

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
- À chaque tick : `now_paris = datetime.now(PARIS_TZ)` (`ZoneInfo("Europe/Paris")`, constante module) →
  `get_users_to_remind(hour, minute)`. Corrigé (2026-09-18) : c'était un décalage UTC+1 fixe, donc décalé
  d'une heure pendant l'heure d'été (fin mars-fin octobre) — `ZoneInfo` gère CET/CEST automatiquement
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
reminder_hour        SMALLINT DEFAULT 7      ← heure de Paris (Europe/Paris, PARIS_TZ dans main.py)
reminder_minute      SMALLINT DEFAULT 30
reminder_last_sent_at DATE    NULL
```

**Défaut** : activé à 7h30 heure de Paris dès la fin de l'onboarding (colonnes initialisées avec
`server_default`).

### Variable Reward — notification post-ride (`app/providers/analysis/highlight.py`)
- `select_highlight(analyzed, fitness, weekly_snap)` → `HighlightResult` — tirage pondéré parmi 6 catégories
- `detect_personal_records(all_logs, *, session_type_real, tss, intensity_factor, intervals_consistency_index, respect_zones_score, current_log_id)` → `PersonalRecord | None` — in-memory, pas de requête DB ; prend des primitives, pas un objet `AnalyzedSession` fabriqué (spec 002 T064)
- Notification en 3 messages : A (teaser, silencieux) → B (métrique héros, silencieux) → C (verdict + clavier RPE, seule vibration)
- Post-RPE : `edit_text` sur Message C → "Ton coach analyse..." → edit → récit LLM 3 phrases (aucune notification supplémentaire)

## Flux de configuration — `/setup`, `/goal`, `/reset`, `/voice` (spec 007)

**`/setup` est une confirmation, pas un interrogatoire.** Il lit `GET /athlete` (FTP, LTHR, FC max, FC
repos, poids, sexe, **âge depuis `icu_date_of_birth`**), le montre, puis ne demande que ce qu'aucune source
ne connaît.

```
/setup → read_athlete_profile() → CONFIRM_PROFILE → [CORRECT_VALUE]
       → GOAL → DATE → VOLUME (voulu) → AVAILABLE_DAYS (jours dispo) → CONSTRAINTS
       → _finalize_setup() → plan → disclaimer (1×)
```

- **AVAILABLE_DAYS** (ajouté 2026-09-18) : grille de jours à cocher/décocher, pré-sélectionnée sur
  mar/jeu/sam/dim (même philosophie que CONFIRM_PROFILE — montrer une valeur plausible, laisser corriger),
  minimum 2 jours (plancher réel de `_build_sessions`). Avant ça, `_build_profile()` hardcodait ces 4 jours
  pour tout le monde sans jamais demander — `plan_builder.py` a un vrai fallback pour une liste vide, mais
  elle n'était jamais vide en pratique (confirmé en lisant le code, pas juste supposé).
- Seuils lus depuis `sportSettings[]` (entrée cyclisme par `types`), **pas** le `icu_ftp` racine (null).
  `app/providers/intervals/athlete_profile.py` — mapping pur, chaque champ porte son origine ; absent =
  jamais un défaut silencieux (FR-008).
- `_build_profile()` prend les valeurs confirmées → `*_source == "source"` (nouvelle valeur du `Literal`).
  `hr_rest` = vrai `icu_resting_hr`.
- **Correction (FR-007)** : `CORRECT_VALUE` capture la valeur voulue mais **garde celle de la source** ;
  l'athlète est renvoyé sur intervals.icu. Aucune divergence locale (Constitution IV). Écriture-retour =
  différée (voir statut en tête).
- `parse_goal_date()` : passé rejeté, < 21 j re-confirmation, > 365 j averti (FR-015). Partagé avec `/goal`.
- Récap « ce sur quoi j'ai construit ton plan » après génération (`_built_from_recap`, FR-004).

**`/goal`** (`app/bot/routers/goal.py`, `GoalStates`) — changer d'objectif sans rien perdre : relit la
source, demande objectif + date seulement, régénère depuis la forme actuelle (`get_current_fitness`, jamais
zéro), garde 100 % de l'historique (les `session_logs` etc. pointent vers l'ancien plan désactivé). Signale
un calendrier périmé via `check_divergence` de spec 005 (FR-014). Résumé « change / gardé » (FR-016).

**`/reset`** (`app/bot/routers/reset.py`, `ResetStates.CONFIRM`) — action **distincte** de `/goal` : liste
chiffrée de ce qui sera supprimé, confirmation tapée `SUPPRIMER`, puis `user_repo.purge_athlete_data`
(11 tables par-athlète, **aucun appel sortant** — vérifié par scan AST). `sync_state` est de ces 11 : sans
ça `history_import_complete` restait à `True` après la purge d'`activities`, donc `import_history()`
ne relançait jamais le réimport des ~120 jours d'historique au `/setup` suivant (bug réel, corrigé après
coup — pas repéré au moment où spec 007 a écrit `/reset`). Garde `coach_voice` et
`disclaimer_acknowledged_at` (identité, pas données d'entraînement) **et** `meal_entries` (spec 008 —
historique perso, ni donnée d'entraînement ni identité, gardé par demande explicite de l'athlète ;
`MealEntry` n'est délibérément pas dans `_PURGE_MODELS`, vérifié par test). `onboarding_completed_at` remis
à `None` → prochain `/setup` = vrai premier run.

**`/voice`** (`app/bot/routers/voice.py`) — liste `personas/*.yaml` avec leur descripteur, écrit
`users.coach_voice`, effet au message suivant (colonne lue par requête, pas de redémarrage).
`services/coach_voice.py::resolve_voice(user)` → `(Persona, fell_back)` : `coach_voice` → `settings.persona`
(défaut `pace`) → `coach-default`. Une voix introuvable → défaut + notice (FR-026). `build_ux_system_prompt`
et `build_system_prompt` prennent un `persona=` optionnel ; sans lui, l'ancien texte « Pace » en dur.
`_MODE_PERSONA` (modes narratifs) reste un axe séparé, non fusionné.

## Mémoire du coach (hors spec — jamais documenté avant, construit le 2026-08-27)

Trois horizons, pas un seul :
- **Court terme** : les 8 derniers messages de chat (`repo.chat_repo.get_conversation(..., limit=8)` dans
  `app/llm/chat.py`), sans résumé — au-delà, perdu.
- **Long terme** : outil LLM `update_coach_memory` (`app/llm/tools.py`) — le modèle mémorise une
  observation **durable** (pattern de fatigue récurrent, contrainte physique confirmée, préférence de
  communication, événement marquant), jamais un état transitoire (fatigue du jour, météo). Deux formes,
  toutes deux sur `AthleteProfile` (colonnes JSON `coach_memory`/`athlete_notes`,
  `app/db/models/profile.py`) :
  - `action: add_note` → ajoute une note catégorisée (`fatigue`/`motivation`/`physique`/`event`/
    `preference`) à la liste `coach_memory`
  - `action: update_athlete_notes` → écrit/écrase une clé stable dans le dict `athlete_notes`
  - Max 1 appel par conversation (consigne dans la description de l'outil, pas appliqué côté code).
  - Relu à **chaque** tour de chat et réinjecté dans `build_system_prompt(coach_memory=…,
    athlete_notes=…)` (`chat.py` ~ligne 159) — donc disponible au modèle indéfiniment, pas juste tant que
    ça reste dans la fenêtre des 8 messages.
- **Moyen terme** : n'existe pas. Pas de résumé glissant des dernières semaines entre les deux horizons
  ci-dessus — identifié comme piste d'amélioration, pas encore scopé.

Purgé par `/reset` comme le reste du profil (`AthleteProfile` est dans `_PURGE_MODELS`, pas de traitement
spécial pour `coach_memory`/`athlete_notes`).

Lecture manuelle (hors Telegram) : `python -m scripts.coach_memory_state --describe` — lit directement
`athlete_profiles.coach_memory`/`.athlete_notes`, même chemin que `build_system_prompt()`.

## Mesure du coût LLM (hors spec — construit le 2026-09-18)

Chaque appel API renvoie ses tokens (`usage.prompt_tokens`/`.completion_tokens`), mais rien ne les gardait
avant ça — juste un log debug perdu. Un tour de chat peut déclencher plusieurs appels API (tool call,
fallback sur contenu vide, appel final après tool call en texte, fallback de fin de boucle) —
`app/llm/chat_client.py::run_agentic_loop()` les cumule tous (`usage_total`, 4ᵉ élément du tuple retourné)
plutôt que de ne garder que le dernier. Propagé par `run_chat()` (`app/llm/chat.py`) jusqu'à
`app/bot/routers/chat.py`, qui l'écrit sur la ligne `role="assistant"` de `chat_messages`
(`tokens_input`/`tokens_output`, colonnes nullable — `NULL` sur les messages `role="user"` et sur tout ce
qui a été créé avant cette migration).

**Portée volontairement limitée** : seule la boucle agentique du chat est mesurée. Les appels
générationnels "one-shot" (narratif de plan `app/llm/narrator.py`, feedback post-activité
`app/llm/activity_analysis.py`) passent par l'interface plus fine `app/llm/providers/*.py`
(`LLMProvider.generate() -> str`) qui ne renvoie pas `usage` à l'appelant — étendre ça n'était pas
nécessaire pour répondre à la question posée (le chat reconstruit le system prompt complet à chaque
message, donc c'est là que le gros du coût récurrent se trouve).

**Lecture** : `python -m scripts.token_usage_state --describe [--days N]` — totaux et moyenne par jour,
lecture seule (`app/db/repositories/chat_repo.py::token_usage_by_day()`, un jour sans tour de chat est
absent du résultat, jamais affiché à 0 token — même convention que `meal_entry_repo.daily_totals`).

**Étape suivante, pas encore faite** : ces chiffres sont ce qu'il faut pour juger si le prompt caching
(préfixe stable du system prompt devant, volatile derrière) vaut le coût de l'implémenter — pas fait ici,
volontairement, cette tâche ne visait que la mesure.

## Observabilité LLM (hors spec — construit le 2026-09-19)

Tracing optionnel des appels LLM (prompts, réponses, tokens, latence, coût) via
**Phoenix** (Arize, self-hosted) — pas un service géré par ce repo, un ajout
d'infra séparé que chaque self-hoster déploie (ou non) à côté. Choisi après avoir
écarté Langfuse : la version "légère" (Postgres + une image, v2) n'a plus de patch de
sécurité depuis fin Q1 2025 ; la version maintenue (v3/v4) exige 6 conteneurs
(Postgres, ClickHouse, Redis, MinIO, web, worker). Phoenix tourne en un seul conteneur
avec persistance SQLite — même philosophie que Banister lui-même :
```yaml
# docker-compose.yml d'un déploiement Phoenix séparé (pas dans ce repo)
services:
  phoenix:
    image: arizephoenix/phoenix:latest
    ports: ["127.0.0.1:6006:6006", "127.0.0.1:4317:4317"]
    environment: ["PHOENIX_WORKING_DIR=/mnt/data"]
    volumes: ["phoenix_data:/mnt/data"]
    networks: ["observability"]
volumes: {phoenix_data: {}}
networks: {observability: {external: true}}
```

**Off par défaut** (`PHOENIX_ENABLED=false`) — un `docker compose up` tout neuf n'a ni
Phoenix, ni l'extra `observability` installé, ni le réseau Docker ci-dessous, et n'est
pas affecté : `app/observability.py::setup_observability()` avale toute exception
d'initialisation (paquets absents, collecteur injoignable...) sans jamais bloquer le
démarrage. Pour l'activer : `uv sync --extra observability` (ou build Docker avec
`--build-arg INSTALL_EXTRAS=observability` — voir `pyproject.toml`), déployer un
Phoenix comme ci-dessus, puis `PHOENIX_ENABLED=true` + le réseau Docker partagé
ci-dessous.

**Trois chemins d'appel LLM, deux instrumentés automatiquement** :
- `app/llm/providers/anthropic.py` (SDK `anthropic`) et `app/llm/chat_client.py` (SDK
  `openai`, `AsyncOpenAI` pointé sur OpenRouter — boucle agentique du chat) : patchés
  par `register(auto_instrument=True)` via les paquets `openinference-instrumentation-
  anthropic`/`-openai`, aucune ligne changée dans ces fichiers. `anthropic` doit rester
  **>=1.0.0** (contrainte de l'instrumenteur) — l'API Messages utilisée ici n'a pas
  changé depuis la 0.x, vérifié par la suite de tests complète après le bump.
- `app/llm/providers/openrouter.py` (`generate()`, appels ponctuels — narrateur, feedback
  post-activité) : `httpx.AsyncClient` brut, aucun SDK à patcher → span OpenInference
  manuel (`tracer.start_as_current_span`, attributs `input.value`/`output.value`/
  `llm.token_count.*`), posé autour de `_post_with_retries()` pour couvrir les 3
  tentatives d'un seul appel logique. Le tracer est un no-op tant que
  `setup_observability()` n'a pas tourné (`PHOENIX_ENABLED=false` ou import isolé, ex.
  un script lancé via `docker exec` sans passer par `app.main`) — aucun `if` nécessaire
  dans ce fichier pour gérer le cas désactivé.

**Réseau Docker** : Phoenix et `banister_app` sont deux projets `docker compose`
séparés — à connecter via un réseau externe partagé (`docker network create
observability`, une fois), sinon `banister_app` ne peut pas atteindre Phoenix.
`PHOENIX_COLLECTOR_ENDPOINT` pointe sur `http://phoenix:6006/v1/traces` (nom DNS du
conteneur sur ce réseau), jamais `localhost` — dans `banister_app`, `localhost` désigne
le conteneur lui-même, pas l'hôte ni Phoenix. Ce wiring (le réseau + l'extra Docker
build) est volontairement **hors du `docker-compose.yml` commité** — il n'a de sens que
si Phoenix est effectivement déployé, et un `docker compose up` par défaut doit rester
autonome pour n'importe quel self-hoster. Un `docker-compose.override.yml` local
(chargé automatiquement par `docker compose`, non commité — voir `.gitignore`) est le
bon endroit pour l'ajouter à son propre déploiement ; s'inspirer du réseau/`build.args`
ci-dessus.

**Doit tourner avant tout appel LLM** : `setup_observability()` est appelé en toute
première instruction de `lifespan()` (`app/main.py`) — `register()` patche les classes
des SDK anthropic/openai en place, donc l'ordre par rapport à `get_provider()` (lazy,
`lru_cache`) ne compte pas tant que c'est fait avant le premier message traité.

## Suivi calorique (spec 008)

L'athlète décrit ce qu'il a mangé en langage naturel dans le chat — pas de commande dédiée, pas de FSM.
Trois outils LLM de plus (8 au total) dans `app/llm/tools.py` / `app/llm/chat.py`, même mécanisme que les
5 outils existants (le LLM extrait, une fonction Python déterministe persiste et calcule).

- **`log_meal`** — repas isolé ou récap de journée. L'estimation calorique vient de la connaissance du LLM
  (pas de base nutritionnelle externe, choix explicite v1) — **hors du périmètre du Principe I**, qui porte
  sur la charge d'entraînement, pas la nutrition. Le **total du jour**, lui, reste une somme SQL
  déterministe recalculée après chaque insertion (`meal_entry_repo.daily_totals`), jamais additionnée par
  le modèle. Un récap de journée (`entry_type=day_recap`) **remplace** les entrées déjà loggées ce jour-là
  au lieu de s'y ajouter — sinon double-comptage.
- **`undo_last_meal_entry`** — supprime la dernière entrée loggée aujourd'hui (correction = annuler puis
  reloguer, pas d'édition en place).
- **`get_calorie_history`** — total jour par jour sur une période ; un jour sans entrée est explicitement
  `"logged": false`, jamais affiché comme 0 kcal.

**Délibérément non fait** : aucune de ces trois figures n'est vérifiée par
`app/services/response_verification.py` (spec 006) — ce module est ancré sur un vocabulaire
d'entraînement (CTL/ATL/TSB/FTP/ACWR/monotonie/VFC/FC repos) et le rester tant que le suivi calorique reste
standalone. Aucun changement à `/recap` ni au system prompt du chat — intégration explicitement différée.

**Rappel du soir** — `app/main.py::_nutrition_reminder_scheduler` envoie un message vers 22h00 heure de
Paris (`PARIS_TZ`, même `ZoneInfo("Europe/Paris")` que `_run_session_reminders` depuis la correction du
2026-09-18 — c'était un décalage UTC+1 fixe avant, voir research R4 de spec 008) si rien n'a été loggé ce
jour-là. Pas de nouvelle colonne : l'absence de ligne `meal_entries` pour aujourd'hui **est** l'état « pas
encore fait » (`app/services/nutrition_reminder.py::needs_reminder`). Pas de `/commande` pour changer
l'heure — fixe pour cette version.

**Script** : `scripts/nutrition_state.py --describe` (totaux récents, lecture seule).

## Mode libre — coaching sans objectif (spec 009)

Avant spec 009, le coach n'était vraiment utile qu'avec un plan actif : sans plan, une activité publiée
sur intervals.icu était **silencieusement droppée** (`SessionLog.plan_id` était `NOT NULL`, donc rien
n'était loggé — voir `app/providers/intervals/notifier.py`, ancien commentaire « SessionLog.plan_id is
NOT NULL »). Spec 009 ajoute un second mode, symétrique au mode objectif existant.

**Mode dérivé, jamais stocké** (`app/services/coaching_mode.py`) :
```python
mode = "goal" if plan_repo.get_active_plan(...) is not None else "freestyle"
```
Pas de nouvelle colonne ni de nouvel état FSM — `training_plans.is_active` porte déjà cette information.
`mode_from_plan(plan)` évite une requête redondante quand l'appelant a déjà chargé le plan (`chat.py`).

**Bascule** : une seule commande, `/goal`, dans les deux sens (pas de nouvelle commande) —
`app/bot/routers/goal.py` :
- Objectif → Libre : 5ᵉ option du clavier `_goal_kb()` (`goal:type:freestyle`) → désactive le plan
  (`plan_repo.deactivate_all_for_user`) + retire automatiquement les séances futures publiées au
  calendrier (réutilise `withdraw_all_publications()`, le chemin de `/unpublish`) + résumé de ce qui est
  gardé. Idempotent — un athlète déjà en mode libre reçoit juste « Déjà en mode libre. ».
- Libre → Objectif : flow `/goal` existant inchangé, sauf le blocage `if plan is None` retiré (c'était
  exactement ce qui empêchait cette direction) ; `_regenerate()` saute le paragraphe « ce qui change » s'il
  n'y a pas d'ancien plan à comparer.

**Suggestion de séance à la demande** (`app/engine/freestyle_selector.py`, zéro LLM — Principe I) :
`choose_workout_type()` remplace la phase de périodisation par l'état de forme (TSB, aligné sur les mêmes
bandes que `atl_ctl.tsb_label()`) + `days_since_hard_effort()` (proxy TSS/heure ≥ 70, car `Activity`
n'a pas de `session_type_real` contrairement à `SessionLog`) comme entrée de sélection. Respecte une
préférence déclarée via `athlete_notes["disliked_workout_types"]` (liste séparée par virgules, écrite par
l'outil `update_coach_memory` existant — pas de nouveau mécanisme de préférence). `build_freestyle_suggestion()`
tourne ensuite le template choisi parmi ceux du type retenu (variété jour par jour, déterministe via
`day_ordinal`) et appelle `fitting.fit_template()` — **sans le brancher à `generate_plan()`**, qui reste un
chantier séparé. Exposé au chat via l'outil LLM `get_freestyle_session_suggestion`, disponible uniquement
en mode libre (`tools.py::tools_for_mode()`, symétrique pour les 3 outils qui n'ont de sens qu'avec un
plan) — voir spec 011 ci-dessous pour ses 3 paramètres optionnels (spec 009 livrait la version sans
paramètre, tout venait du serveur). Le `target_tss` retourné est enregistré dans le
`MetricRegistry` de la vérification de réponse (spec 006) comme n'importe quelle autre métrique — durée et
zone ne le sont jamais (déjà exclues structurellement par `response_verification.py`, R5).

**Cible de charge par type — hors `long_ride`** (`_TSS_FLOOR`/`_TSS_CTL_MULTIPLIER`) : `target_tss = max(floor,
CTL × coefficient)`, un engineering guess assumé comme tel — aucune règle "TSS d'une séance = fraction de
CTL" n'est documentée nulle part (vérifié 2026-09-20), contrairement au TSS/heure par zone (`tss.py::ZONE_IF`,
lui sourcé Coggan/TrainingPeaks — IF² × 100). Pas retouché faute de mieux à sourcer ; à revisiter si une
meilleure référence apparaît.

**`long_ride` — cas à part, durée d'abord** (corrigé 2026-09-20, trouvé en test live) : l'ancien
`_TSS_CTL_MULTIPLIER["long_ride"] = 1.4` donnait à un athlète déconditionné (CTL bas après une coupure) une
« sortie longue » de ~65min — jamais sourcé, et contraire à la définition même de « longue » (Friel :
30min à 2h+ selon l'objectif, toujours la sortie la plus longue de la semaine, indépendamment de la charge
courante). `_long_ride_target_tss()` inverse la logique : durée d'abord (plancher `LONG_RIDE_MIN_MINUTES`
= 120min, jamais dérivé de CTL), TSS dérivée via `tss.estimate_session_tss("Z2", durée, …)` — la même
fonction que `fit_template()` utilise en interne, donc la durée qui ressort correspond exactement à celle
demandée (pas de dérive entre deux constantes zone/TSS différentes). Une durée demandée (`available_minutes`)
au-dessus du plancher est honorée telle quelle ; en dessous, le plancher gagne — `fit_template()` refuse
honnêtement plutôt que de servir une « longue » plus courte que ce mot ne veut dire (Principe IV).

**Feedback post-activité en mode libre** (`app/services/activity_feedback.py`) : l'outcome `"no_plan"` est
retiré, remplacé par `"freestyle"` — `_assemble_freestyle_feedback()` logge la séance
(`plan_id`/`week_number`/`day_of_week` à `NULL`, `status="unplanned"`), calcule forme + highlight comme le
chemin « matched », mais ne prétend jamais avoir tenté un matching. `notifier.py` livre une vraie
notification (« Activité enregistrée », jamais « hors plan »). Idempotence conservée : le garde-fou de
relecture d'une activité déjà loggée s'applique aussi aux logs en mode libre.

**Garde-fous et mémoire du coach : aucun changement** — `guardrail_service.py` était déjà tolérant à
`plan is None` (`plan_start = plan.start_date if plan is not None else today`), vérifié par test de
régression plutôt que supposé (`tests/test_services/test_guardrail_service.py`).

**Migration** : `session_logs.plan_id`/`.week_number`/`.day_of_week` passés en `nullable=True`
(`migrations/versions/ec93c120b7ab_*.py`). ⚠️ SQLite ne supporte pas `ALTER TABLE ... ALTER COLUMN`
directement (même limite déjà rencontrée en spec 002, `b24778c0a229`) — la migration utilise
`op.batch_alter_table()`, pas la forme autogénérée brute.

**Non fait délibérément** : pas de rappel proactif en mode libre (à la demande uniquement — voir
`specs/009-freestyle-coaching-mode/spec.md`, Hypothèses) ; pas de persistance de la suggestion (recalculée
à chaque demande, comme `/forme`).

## Publier une séance mode libre (spec 010)

Le mode libre (spec 009) proposait une séance en chat mais rien ne permettait de l'exécuter réellement
(Garmin, home trainer) — la suggestion n'était jamais écrite nulle part. Spec 010 ajoute la publication
d'une séance ponctuelle une fois que l'athlète l'a négociée en chat et confirmée.

**Confirmation = bouton, pas un outil LLM de plus** (décidé après avoir pesé le compromis avec
l'utilisateur) : `_tool_get_freestyle_session_suggestion` (spec 009) tague désormais son résultat
`"type": "freestyle_publish"` + un `"id"` court (`uuid4().hex[:8]`), qui rejoint le mécanisme
`pending_proposal` déjà existant pour `propose_plan_modification`/`propose_session_adjustment`
(`app/llm/chat.py`). **Différence volontaire avec ce mécanisme** : `app/bot/routers/chat.py` stocke
`pending_freestyle_id`/`pending_freestyle_suggestion` en **données FSM**, sans jamais passer par
`PlanStates.PENDING_MODIFICATION` — cet état-là bloque le chat tant que l'athlète n'a pas tranché, ce qui
casserait la négociation ("propose-moi autre chose", spec 010 US2). Le clavier attaché au message
(`freestyle:publish:<id>`) reste donc utilisable pendant que la conversation continue normalement.

**`freestyle:publish:<id>` callback** (`app/bot/routers/chat.py::cb_publish_freestyle`, non scopé à un
`StateFilter` — volontaire) : compare l'id tapé à `pending_freestyle_id` ; différent ou absent (une suggestion
plus récente l'a remplacée) → alerte « n'est plus la plus récente », rien n'est écrit. Sinon : calcule les
zones depuis le profil (`compute_power_zones`/`compute_hr_zones` — pas de plan pour les fournir, contrairement
au mode objectif), rend le DSL, publie via `publish_freestyle_session()`. Échec → message d'échec explicite,
`pending_freestyle_id` **conservé** (retaper est une vraie relance) ; succès → effacé (retaper n'a plus rien
à matcher).

**Écriture calendrier — nouvelles fonctions, pas de modification du chemin `/publish` existant**
(`app/services/publication.py`) :
- `publish_freestyle_session()` — toujours une création, jamais un diff (une séance mode libre n'a rien à
  réconcilier, contrairement à `publish_sessions()` sur l'horizon d'un plan) ; réutilise `render_dsl()` et
  la gestion `push_errors` telle quelle.
- `withdraw_freestyle_publications()` — miroir de `withdraw_all_publications()`, appelé par `/unpublish`
  quand il n'y a pas de plan actif (`app/bot/routers/publish.py`, callback `pub:withdrawall_freestyle`) et
  automatiquement par `app/bot/routers/goal.py::_regenerate()` en quittant le mode libre (FR-011,
  symétrique du retrait des séances de plan à l'entrée en mode libre, spec 009 FR-013).

**Nouvelle table plutôt que retrofit** : `freestyle_published_entries`
(`app/db/models/publication.py::FreestylePublishedEntry`, `app/db/repositories/freestyle_publication_repo.py`)
— **pas** `PublishedEntry` (spec 005), qui a `plan_id`/`approval_id` NOT NULL. Même raisonnement que
`SessionLog.plan_id` en spec 009 : forcer une séance mode libre dans un schéma pensé pour un plan/une
approbation par lot serait une fiction silencieuse (Principe IV). Préfixe `banister:freestyle:` (via
`calendar.py::build_freestyle_external_id()`), garantie de propriété identique à `PublishedEntry`.

**Non fait délibérément** : pas de chemin de confirmation en langage naturel ("publie-la" tapé en chat) —
le bouton a été choisi précisément pour rendre la confirmation sans ambiguïté par construction ; pas de
diff/mise à jour d'une séance déjà publiée (toujours une nouvelle création, jamais un `update_event`).

## Négociation de séance en mode libre (spec 011)

Avant spec 011, `get_freestyle_session_suggestion` (spec 009) ne prenait aucun paramètre — une demande
explicite de l'athlète en chat ("je veux faire des intervalles", "j'ai 45 minutes ce soir") était
totalement ignorée, seul l'état de forme comptait. Spec 011 laisse le LLM extraire 3 signaux optionnels du
message et les transmettre à l'outil, **sans qu'aucun calcul ne change de camp** (Principe I) : `target_tss`
et la durée restent produits par `app/engine/freestyle_selector.py`, seuls les paramètres d'entrée varient.

**`requested_workout_type` prioritaire mais jamais bloquant** (`choose_workout_type()`) : contourne
carrément `avoid_workout_types` (une demande explicite du tour courant prime sur une préférence durable —
la préférence elle-même n'est ni lue ni modifiée dans ce cas). `WorkoutTypeChoice.default_conflicts` est
`True` uniquement quand une demande explicite diverge de ce que la forme seule aurait choisi — **jamais**
quand c'est `avoid_workout_types` qui décale le choix sans demande explicite (bug attrapé par un test avant
livraison : les deux mécanismes doivent rester indépendants, sinon le chemin sans demande — spec 009 —
regresserait silencieusement). `build_freestyle_suggestion()` ajoute alors une phrase à `reasoning_summary`
(même pattern que la note existante pour `preference_overridden`), jamais un champ séparé.

**`style_preference` — texte libre, résolu par un second appel LLM isolé** (remplace l'enum `template_id`
le 2026-09-20). L'ancienne version collait le catalogue complet des 18 templates (~9,4k chars, purpose/
intent/suits) dans la description JSON-Schema du paramètre `template_id`, donc renvoyé dans `tools=` à
**chaque** appel API du chat en mode libre (jusqu'à 3 par tour, aucun prompt caching) — et grossissant
linéairement avec `sessions/*.yaml`. Maintenant : le modèle du chat recopie la préférence de l'athlète
verbatim (« pas de pyramide ») dans `style_preference`, sans jamais voir le catalogue. Si le champ est
rempli, `_tool_get_freestyle_session_suggestion` (`app/llm/chat.py`) résout d'abord le `workout_type`
via `choose_workout_type()` (pure, recalculé à l'identique par `build_freestyle_suggestion()` ensuite),
puis `app/llm/template_picker.py::pick_template()` fait **un** appel `LLMProvider.generate()` one-shot
(même mécanisme que `narrator.py`, pas la boucle agentique, pas le system prompt du chat) avec un
prompt minimal : la préférence + les seuls candidats du type retenu (`candidates_for()`, 1–13 templates).
Réponse attendue : un id ou `NONE`. Hors liste, `NONE`, provider en erreur, préférence vide → `None`,
jamais d'exception ; un seul candidat → renvoyé sans appel. Le résultat est passé en
`requested_template_id` — chemin `build_freestyle_suggestion()` inchangé (un id hors type reste ignoré,
rotation `day_ordinal` comme si rien n'avait été demandé, FR-005). Coût : cas courant (pas de préférence)
= zéro catalogue ; cas rare = +1 appel de quelques centaines de tokens. `max_tokens` du picker =
`settings.llm_max_tokens`, pas un cap serré — un modèle à raisonnement caché renverrait un contenu null
(voir Stack, `LLM_MAX_TOKENS`).

**`max_duration_minutes`** : aucun nouveau paramètre côté moteur — réutilise `available_minutes`, déjà
présent sur `build_freestyle_suggestion()`/`fit_template()` depuis spec 004 mais jamais alimenté par l'outil
mode libre jusqu'ici. `NoSuitableTemplateError` (déjà existante) couvre le cas où rien ne rentre dans le
délai indiqué, même après relâchement de tolérance.

**Un seul appel outil, pas de va-et-vient** : les 3 paramètres sont optionnels sur le même appel
`get_freestyle_session_suggestion` — jamais un second tool call "liste puis choix". `app/llm/chat.py::
_tool_get_freestyle_session_suggestion` se contente de transmettre `args` tel quel à
`build_freestyle_suggestion()` ; toute la logique de repli (type non supporté, template hors-liste, durée
impossible) vit dans `app/engine/freestyle_selector.py`, jamais dans `llm/` (Principe III).

## Relecture de séance — `/review` (hors spec — arrivé du remote 2026-09-21, jamais documenté avant)

L'athlète relit une séance déjà loggée à la demande : `/review` liste les 5 dernières (`session_log_repo
.get_recent_for_user`) via un clavier inline (`app/bot/keyboards/review.py::recent_sessions_keyboard`,
callback `review:pick:<log_id hex>`), puis génère une synthèse en un seul appel LLM one-shot
(`app/llm/review.py::generate_session_review`, même mécanisme que `narrator.py`/`template_picker.py` —
jamais la boucle agentique, `app/services/session_review.py::assemble_review_context()` assemble tout
avant l'appel). Pas de FSM (même précédent que `freestyle:publish:<id>`, spec 010).

**Un seul mode, pas trois** (simplifié 2026-09-21, décision owner) : la version arrivée du remote proposait
3 profondeurs (`brief`/`default`/`deep`, picker à 2 étapes + raccourci CLI `/review brief`), copiées d'un
ratio observé chez Enduragent (référence open source). Retiré après revue — les 3 modes ne se
différenciaient que par deux consignes de prompt molles (un budget de mots suggéré, une permission de
vocabulaire technique), **jamais appliquées par force** (même `max_tokens` fixe pour les 3, aucune
troncature, structure obligatoire identique en 4 points) : en pratique rien ne garantissait que les 3
sorties diffèrent, et aucun test ne le vérifiait. `build_review_system_prompt(has_rpe)` (`app/llm/
prompts.py`) ne prend plus de paramètre de profondeur — un seul `REVIEW_WORD_BUDGET` (150-200 mots) et un
seul `REVIEW_VOCAB_RULE`.

**Règle non-négociable conservée** : si le RPE n'est pas loggé sur la séance, `REVIEW_RPE_MISSING_RULE`
interdit au LLM de juger "séance réussie" sur les seuls chiffres (durée/TSS/zones ne disent rien de la
fatigue ressentie) — il doit le dire explicitement plutôt que de trancher à la place de l'athlète.

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
LLM_MAX_TOKENS=4000              # optionnel — budget de sortie des appels rédactionnels (narrator,
                                # réponse finale du chat agentique). Défaut 4000 : les modèles
                                # OpenRouter à raisonnement caché épuisent un budget bas avant
                                # d'émettre du contenu → finish_reason=length → fallback silencieux
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...            # si LLM_PROVIDER=anthropic
PHOENIX_ENABLED=false                    # optionnel — active le tracing LLM (voir § Observabilité)
PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006/v1/traces  # optionnel — défaut déjà correct en docker-compose
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
| Modifier le TID / indice de polarisation | `app/engine/tid.py` — seuils dans `app/engine/guardrail_thresholds.py` |
| Modifier la phase diagnostique (`detected_phase`, distincte de `week.phase`) | `app/engine/phase_detection.py` |
| Modifier power-curve delta / sustainability_profile | `app/engine/power_curve.py` — endpoint dans `client.get_power_curves()`, câblé dans `app/bot/routers/forme.py::_fetch_power_profile()` |
| Modifier l'analyse DFA α1 | `app/engine/dfa.py` — normalisation streams dans `app/providers/intervals/streams.py`, câblé dans `app/bot/routers/review.py::_fetch_dfa()` |
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
| Modifier la mesure du coût LLM (tokens) | `app/llm/chat_client.py::run_agentic_loop()` (cumul) + `app/db/repositories/chat_repo.py::token_usage_by_day()` (lecture) |
| Modifier le rendu DSL d'une séance (texte envoyé à intervals.icu) | `app/providers/intervals/workout_dsl.py` — `render_dsl()` |
| Modifier le diff idempotent de publication (create/update/conflict) | `app/providers/intervals/calendar.py` — `publish_sessions()` |
| Modifier la barrière de consentement / le hash de plan | `app/services/publication.py` — `authorize_publication()`, `plan_content_hash()` |
| Modifier le texte de la demande d'approbation `/publish` | `app/services/publication.py` — `build_approval_request_text()` |
| Modifier la détection de divergence plan↔calendrier | `app/services/publication.py` — `check_divergence()` |
| Modifier le flux `/publish` / `/unpublish` (clavier, callbacks) | `app/bot/routers/publish.py` + `app/bot/keyboards/publish.py` |
| Modifier un seuil de garde-fou (ACWR, ramp, monotonie, VFC, FC repos, recovery_index, outlier…) | `app/engine/guardrail_thresholds.py` |
| Modifier un évaluateur de signal (charge ou récup) | `app/engine/guardrails.py` — `evaluate_*` |
| Modifier l'assemblage des signaux / la raison d'insuffisance | `app/services/guardrail_service.py` |
| Modifier la vérification des chiffres de la réponse LLM (ancrage, tolérance, retrait) | `app/services/response_verification.py` |
| Modifier le disclaimer ou les règles no-diagnostic | `app/llm/prompts.py` — `DISCLAIMER_TEXT`, `SCOPE_OF_ADVICE_RULES` |
| Modifier la lecture du profil source (setup) | `app/providers/intervals/athlete_profile.py` — `map_athlete_profile()` |
| Modifier l'écran de confirmation / `_build_profile` (setup) | `app/bot/routers/setup.py` |
| Modifier la question des jours disponibles (setup) | `app/bot/routers/setup.py` — `available_days_keyboard()`, `setup_days_toggle()`/`setup_days_confirm()` |
| Modifier `/goal` (re-plan) ou `/reset` (purge) | `app/bot/routers/{goal,reset}.py` |
| Ajouter / modifier une voix de coach | `personas/*.yaml` (YAML seul, aucun code) — `/voice` la liste |
| Modifier la résolution de voix / le fallback | `app/services/coach_voice.py` — `resolve_voice()` |
| Modifier le suivi calorique (`log_meal`, `undo_last_meal_entry`, `get_calorie_history`) | `app/llm/tools.py` (schémas) + `_tool_log_meal()`/`_tool_undo_last_meal_entry()`/`_tool_get_calorie_history()` dans `app/llm/chat.py` |
| Modifier l'agrégation calorique (total du jour, historique) | `app/db/repositories/meal_entry_repo.py` — `daily_totals()` |
| Modifier le rappel calorique du soir | `app/main.py` — `_nutrition_reminder_scheduler()` / `_run_nutrition_reminders()` ; sélection dans `app/services/nutrition_reminder.py` |
| Modifier la dérivation du mode (libre/objectif) | `app/services/coaching_mode.py` — `get_coaching_mode()`, `mode_from_plan()` |
| Modifier la bascule `/goal` (libre ↔ objectif) | `app/bot/routers/goal.py` — `_enter_freestyle_mode()`, `_goal_kb()` |
| Modifier la sélection de séance en mode libre (type, TSS cible) | `app/engine/freestyle_selector.py` — `choose_workout_type()`, `build_freestyle_suggestion()` |
| Modifier l'outil LLM de suggestion mode libre | `app/llm/tools.py` (schéma + `tools_for_mode()`) + `_tool_get_freestyle_session_suggestion()` dans `app/llm/chat.py` |
| Modifier la négociation mode libre (type/template/durée demandés) | `app/engine/freestyle_selector.py` — `requested_workout_type`/`requested_template_id` sur `choose_workout_type()`/`build_freestyle_suggestion()` ; résolution de `style_preference` → id par second appel LLM dans `app/llm/template_picker.py::pick_template()` |
| Modifier le feedback post-activité en mode libre | `app/services/activity_feedback.py` — `_assemble_freestyle_feedback()` ; copie de notification dans `app/providers/intervals/notifier.py` |
| Modifier la publication d'une séance mode libre (bouton, callback) | `app/bot/routers/chat.py` — `cb_publish_freestyle()` ; tagging dans `app/llm/chat.py::_tool_get_freestyle_session_suggestion` |
| Modifier l'écriture/retrait calendrier d'une séance mode libre | `app/services/publication.py` — `publish_freestyle_session()`, `withdraw_freestyle_publications()` |
| Modifier `/unpublish` en mode libre | `app/bot/routers/publish.py` — `cmd_unpublish()` (branche sans plan actif), `cb_withdraw_all_freestyle()` |
| Ajouter champ DB | `app/db/models/` + `app/db/repositories/` + `alembic revision --autogenerate` |
| Modifier le backup automatique au démarrage (rotation, throttle) | `app/services/backup.py` — `run_startup_backup()` |
| Modifier le tracing LLM (Phoenix) | `app/observability.py` — `setup_observability()` ; span manuel dans `app/llm/providers/openrouter.py` |
| Modifier `/review` (picker, synthèse) | `app/bot/routers/review.py` + `app/llm/review.py` — prompt dans `app/llm/prompts.py::build_review_system_prompt()` |
| Architecture complète | `docs/ARCHITECTURE.md` |
