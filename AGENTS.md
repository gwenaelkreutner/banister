# Banister — instructions agent

Avant toute analyse ou modification, lire entièrement `CLAUDE.md` : c'est la référence
vivante du projet. Les documents `docs/ARCHITECTURE.md` et `docs/BUSINESS_LOGIC.md` sont
historiques et peuvent être obsolètes.

Commencer par vérifier `git status` et préserver les modifications existantes. Pour Python,
utiliser `uv` (par exemple `uv run pytest`), jamais l'environnement global.

Pour une demande produit, expliquer les décisions et leurs impacts ; ne détailler
l'implémentation que si elle est demandée. Après une modification, ajouter le test de
régression pertinent et exécuter les tests ciblés avant de conclure.
