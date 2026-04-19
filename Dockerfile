# Étape 1 : Construction / Installation des dépendances
FROM python:3.13-slim AS builder

# Installation de uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# On copie d'abord uniquement les fichiers de dépendances pour le cache
COPY pyproject.toml uv.lock* ./

# Installation des dépendances (sans installer le projet lui-même)
RUN uv sync --frozen --no-install-project

# Étape 2 : Image finale
FROM python:3.13-slim

WORKDIR /app

# Récupération de l'environnement virtuel créé par uv
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copie du reste du code
COPY . .

EXPOSE 8000

# Lancement avec Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]