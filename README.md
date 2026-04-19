# Banister

Bot Telegram de coaching cyclisme basé sur l'IA. Génère des plans d'entraînement personnalisés, suit la progression, et s'adapte aux retours de l'athlète via une conversation naturelle.

## Fonctionnalités

- **Onboarding Strava** (4 questions) — import automatique de 49 jours d'activités, détection du niveau, volume, FTP et FC max
- **Onboarding classique** (8 questions) — sans Strava
- **Plan d'entraînement personnalisé** — périodisation déterministe (Base/Build/Peak/Taper), zones puissance et FC
- **Logging RPE** — suivi TSS réel après chaque sortie
- **Chat IA** — modification du plan par conversation, questions libres
- **Forme du jour** — ATL/CTL/TSB calculés depuis l'historique

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Bot Telegram | aiogram v3 (async FSM) |
| API | FastAPI |
| Base de données | Supabase (PostgreSQL) |
| ORM | SQLAlchemy (asyncpg) |
| LLM | OpenRouter / Anthropic Claude |
| Intégration sport | Strava API v3 |
| Runtime | Python 3.13 |

## Installation

### Pré-requis

- Python 3.13
- Compte Supabase (ou PostgreSQL)
- Bot Telegram ([@BotFather](https://t.me/BotFather))
- App Strava (optionnel) — [developers.strava.com](https://developers.strava.com)
- Clé API OpenRouter ou Anthropic

### 1. Cloner et installer

```bash
git clone <repo>
cd banister
pip install -e .
```

### 2. Variables d'environnement

Créer un fichier `.env` à la racine :

```env
# Telegram
TELEGRAM_BOT_TOKEN=7443057909:AAF...

# Base de données (Supabase — SSL obligatoire)
DATABASE_URL=postgresql+asyncpg://postgres:<password>@<host>.supabase.co:5432/postgres?ssl=require

# LLM
LLM_PROVIDER=openrouter          # ou "anthropic"
LLM_MODEL=arcee-ai/trinity-large-preview:free
OPENROUTER_API_KEY=sk-or-...
# ANTHROPIC_API_KEY=sk-ant-...   # si LLM_PROVIDER=anthropic
CHAT_MODEL=anthropic/claude-sonnet-4-6  # modèle pour le chat agentique (via OpenRouter)

# Strava (optionnel)
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=abc123...
STRAVA_REDIRECT_URI=https://<votre-domaine>/auth/strava/callback
STRAVA_STATE_SECRET=<secret-aleatoire-32-chars>
STRAVA_WEBHOOK_VERIFY_TOKEN=<token-webhook>

# App
ENVIRONMENT=development
LOG_LEVEL=INFO
```

### 3. Migrations base de données

Exécuter dans l'ordre sur Supabase SQL Editor :

```
migrations/001_add_oauth_connections.sql
migrations/002_add_session_logs.sql
migrations/003_add_activity_details.sql
migrations/004_add_chat_messages.sql
migrations/005_add_environment.sql
migrations/006_add_activities.sql
```

### 4. Lancer en développement

```bash
python -m uvicorn app.main:app --port 8000 --reload
```

Le bot démarre en mode **polling** automatiquement (pas besoin de webhook en dev).

Pour tester le flow OAuth Strava en local, exposer le port avec ngrok :

```bash
ngrok http 8000
# puis mettre l'URL ngrok dans STRAVA_REDIRECT_URI et dans les settings de l'app Strava
```

## Structure du projet

```
app/
├── main.py              # Point d'entrée FastAPI + callback OAuth Strava
├── config.py            # Configuration (pydantic-settings)
├── bot/
│   ├── routers/         # Handlers Telegram (onboarding, plan, chat, strava...)
│   ├── keyboards/       # Claviers inline
│   ├── middlewares/     # Session DB + chargement utilisateur
│   ├── states.py        # États FSM
│   └── setup.py         # Création bot + dispatcher
├── db/
│   ├── models/          # Modèles SQLAlchemy
│   └── repositories/    # Accès base de données
├── engine/
│   ├── plan_builder.py  # Génération du plan
│   ├── plan_modifier.py # Modification via LLM
│   ├── periodization.py # Blocs périodisation
│   ├── zones.py         # Zones puissance/FC
│   ├── tss.py           # Calcul TSS
│   └── atl_ctl.py       # Fitness (ATL/CTL/TSB)
├── llm/
│   ├── chat.py          # Orchestration chat agentique
│   ├── chat_client.py   # Boucle outil LLM
│   ├── prompts.py       # System prompt
│   └── providers/       # Anthropic, OpenRouter
└── strava/
    ├── oauth.py         # Flux OAuth + HMAC state
    ├── client.py        # Appels API Strava
    ├── history.py       # Import historique + calcul TSS
    └── webhook.py       # Réception événements
migrations/              # SQL à exécuter manuellement sur Supabase
tests/                   # Tests engine (zones, TSS, périodisation, plan)
```

## Commandes bot disponibles

| Commande | Description |
|----------|-------------|
| `/start` | Démarrer ou reprendre l'onboarding |
| `/plan` | Voir le programme de la semaine courante |
| `/week N` | Voir la semaine N du plan |
| `/forme` | Voir ATL/CTL/TSB (forme du jour) |
| `/connect_strava` | Connecter son compte Strava |
| `/disconnect_strava` | Déconnecter Strava |
| `/cancel` | Annuler l'action en cours |
| `/help` | Aide |

## Déploiement production

En production, définir `ENVIRONMENT=production` et `TELEGRAM_WEBHOOK_URL=https://<domaine>/webhook/telegram`. Le bot passe automatiquement en mode webhook.

```env
ENVIRONMENT=production
TELEGRAM_WEBHOOK_URL=https://banister.example.com/webhook/telegram
TELEGRAM_WEBHOOK_SECRET=<secret>
```

## Tests

```bash
pytest tests/
```

Les tests couvrent : zones puissance/FC, calcul TSS, périodisation, génération de plan.

## Principe fondateur

> Le LLM ne calcule jamais la charge d'entraînement. Tout calcul (TSS, zones, ATL/CTL) est déterministe. Le LLM sert uniquement à la narration, l'adaptation conversationnelle et la proposition de modifications.
