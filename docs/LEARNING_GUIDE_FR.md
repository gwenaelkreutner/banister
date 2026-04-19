# Guide d'apprentissage (débutant) — Banister

## 1) À quoi sert ce projet ?

Banister est un coach cyclisme dans Telegram :
- il te pose des questions (onboarding),
- génère un plan d'entraînement,
- suit ta charge (TSS, ATL/CTL/TSB),
- et discute en langage naturel pour adapter le plan.

Le principe clé : **les calculs d'entraînement sont déterministes (code Python), le LLM ne fait que le texte et l'orchestration**.

---

## 2) Carte mentale simple de l'architecture

Pense le projet en 5 couches :

1. **Entrée HTTP / démarrage** : `app/main.py` (FastAPI + démarrage bot)
2. **Conversation Telegram** : `app/bot/` (routers, états, middlewares)
3. **Métier entraînement** : `app/engine/` (zones, TSS, périodisation, plan)
4. **Persistance** : `app/db/` (modèles SQLAlchemy + repositories)
5. **IA conversationnelle** : `app/llm/` (prompt, outils, boucle agentique)

Ajoute à cela l'intégration **Strava** dans `app/strava/`.

---

## 3) Les points de repère les plus importants

### Point d'entrée
- `app/main.py` lance le bot en polling (dev) ou configure un webhook (prod).
- Le même process gère aussi les routes OAuth Strava.

### Pipeline message Telegram
- `app/bot/setup.py` assemble le dispatcher.
- Ordre crucial : middlewares DB/user, puis routers.
- Le router de chat est volontairement chargé en dernier (capture générique).

### Données
- Les modèles DB sont dans `app/db/models/`.
- Les accès SQL sont centralisés dans `app/db/repositories/`.
- Exemple important : un plan stocke une version `plan_technical` (JSON) + `plan_narrative` (JSON).

### Moteur d'entraînement (cœur métier)
- `app/engine/plan_builder.py` = fonction principale de génération.
- Règles explicites : progression de zones, limitation intensité, structure des séances.
- C'est cette partie qu'il faut maîtriser en priorité pour comprendre la valeur produit.

### IA (LLM)
- `app/llm/chat.py` prépare le contexte (profil, plan, logs), appelle la boucle agentique.
- `app/llm/chat_client.py` gère les tool calls (max d'itérations, exécution d'outils).
- Le LLM appelle des outils, mais **n'est pas la source de vérité calculatoire**.

---

## 4) Parcours recommandé pour apprendre (ordre conseillé)

1. Lire `README.md` pour la vue globale et les commandes.
2. Lire `app/main.py` pour comprendre le cycle de vie.
3. Lire `app/bot/setup.py`, puis un router simple (`common.py`) et un router métier (`onboarding.py`).
4. Lire `app/engine/plan_builder.py` puis `periodization.py`, `tss.py`, `zones.py`.
5. Lire `app/db/models/` puis `app/db/repositories/` pour relier la logique au stockage.
6. Terminer par `app/llm/chat.py` + `chat_client.py` pour comprendre l'agentic loop.

---

## 5) Bonnes pratiques pour contribuer sans se perdre

- Commencer par modifier **une seule couche** à la fois.
- Quand tu touches au moteur (`app/engine/`), exécuter les tests de `tests/test_engine/`.
- Éviter d'ajouter de la logique SQL dans les handlers Telegram : passer par les repositories.
- Si tu modifies le chat IA, vérifier l'impact sur les outils et les intents.

---

## 6) Check-list “je suis prêt à avancer”

Tu peux passer à des sujets plus avancés quand tu sais :
- expliquer le rôle de `main.py`,
- retracer le chemin d'un message Telegram jusqu'au moteur,
- dire où est stocké un plan et sous quel format,
- justifier pourquoi les calculs (TSS/ATL/CTL) restent déterministes,
- lancer et interpréter les tests du moteur.
