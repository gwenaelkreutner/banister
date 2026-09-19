# Étape 1 : Construction / Installation des dépendances
FROM python:3.13-slim AS builder

# Installation de uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# On copie d'abord uniquement les fichiers de dépendances pour le cache
COPY pyproject.toml uv.lock* ./

# Installation des dépendances (sans installer le projet lui-même). Extra optionnel
# (ex. --build-arg INSTALL_EXTRAS=observability pour le tracing Phoenix, voir
# CLAUDE.md § Observabilité) — vide par défaut, image de base sans dépendances
# inutilisées pour qui ne l'active pas.
ARG INSTALL_EXTRAS=
RUN uv sync --frozen --no-install-project ${INSTALL_EXTRAS:+--extra $INSTALL_EXTRAS}

# Étape 2 : Image finale
FROM python:3.13-slim

WORKDIR /app

# Récupération de l'environnement virtuel créé par uv
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
# Sans ça, stdout est bufferisé par bloc (pas de TTY dans le conteneur) — un crash
# rapide au démarrage peut mourir avant que le traceback soit flush vers les logs
# Docker. Trouvé en réel : un crash-loop sous restart:always ne montrait AUCUNE
# erreur dans `docker compose logs`, juste le cycle de démarrage qui repartait.
ENV PYTHONUNBUFFERED=1

# Copie du reste du code
COPY . .

EXPOSE 8000

# Lancement avec Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]