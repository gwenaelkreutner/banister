"""
Client Strava API.

Fournit :
  - get_athlete() : récupère le profil de l'athlète
  - ensure_valid_token() : rafraîchit le token si nécessaire et retourne un access_token valide
"""

from datetime import datetime, timedelta, timezone

import httpx

from app.db.models.oauth_connection import OAuthConnection
from app.db.repositories import oauth_repo
from app.strava.oauth import refresh_tokens

_STRAVA_API_BASE = "https://www.strava.com/api/v3"
_TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)


async def get_athlete(access_token: str) -> dict:
    """Retourne le profil de l'athlète depuis l'API Strava."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{_STRAVA_API_BASE}/athlete",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()


async def get_athlete_stats(access_token: str, athlete_id: int) -> dict:
    """Retourne les statistiques globales de l'athlète (ytd, recent, all-time)."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{_STRAVA_API_BASE}/athletes/{athlete_id}/stats",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()


async def ensure_valid_token(conn: OAuthConnection, session) -> str:
    """
    Vérifie que le token n'est pas expiré.
    Si expiré (ou proche de l'expiration), rafraîchit et sauvegarde en DB.
    Retourne un access_token valide.
    """
    now = datetime.now(timezone.utc)
    if conn.token_expires_at - _TOKEN_EXPIRY_BUFFER > now:
        return conn.access_token

    new_tokens = await refresh_tokens(conn.refresh_token)
    await oauth_repo.update_tokens(session, conn, new_tokens)
    return new_tokens["access_token"]
