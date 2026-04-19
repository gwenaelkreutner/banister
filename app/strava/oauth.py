"""
Flux OAuth Strava.

Flux :
  1. build_auth_url(telegram_id) → URL à envoyer à l'utilisateur
  2. Strava redirige vers /auth/strava/callback?code=...&state=...
  3. verify_state(state) → telegram_id (ou None si HMAC invalide)
  4. exchange_code(code) → dict avec access_token, refresh_token, expires_at, athlete
  5. refresh_tokens(refresh_token) → dict avec les nouveaux tokens
"""

import hashlib
import hmac
import urllib.parse

import httpx

from app.config import settings

_STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
_STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


# ── State HMAC ────────────────────────────────────────────────────────────────

def build_auth_url(telegram_id: int, context: str = "main") -> str:
    """Construit l'URL OAuth Strava avec un state signé HMAC.

    context="main"       → connexion post-onboarding (comportement existant)
    context="onboarding" → connexion pendant l'onboarding, déclenche l'import historique
    """
    state = _sign_state(telegram_id, context)
    params = {
        "client_id": settings.strava_client_id,
        "redirect_uri": settings.strava_redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "read,activity:read_all,profile:read_all",
        "state": state,
    }
    return f"{_STRAVA_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _sign_state(telegram_id: int, context: str = "main") -> str:
    """Retourne '{telegram_id}:{context}.{hmac_hex}'.

    Le HMAC signe le payload complet '{telegram_id}:{context}'.
    Format rétrocompatible avec l'ancien '{telegram_id}.{hmac_hex}'
    (les anciennes URLs ont context="main" implicite, elles échoueront à la vérification
    car le payload a changé — comportement attendu : lien expiré).
    """
    key = settings.strava_state_secret.encode()
    payload = f"{telegram_id}:{context}"
    sig = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_state(state: str) -> tuple[int, str] | None:
    """Vérifie le HMAC du state.

    Retourne (telegram_id, context) si valide, None sinon.
    Supporte les deux formats :
      - Nouveau : '{telegram_id}:{context}.{hmac_hex}'
      - Ancien  : '{telegram_id}.{hmac_hex}' → context="main" (rétrocompat)
    """
    try:
        payload, sig = state.rsplit(".", 1)
        parts = payload.split(":", 1)
        telegram_id = int(parts[0])
        context = parts[1] if len(parts) > 1 else "main"
    except (ValueError, AttributeError):
        return None

    expected = _sign_state(telegram_id, context)
    if not hmac.compare_digest(state, expected):
        return None
    return telegram_id, context


# ── Token exchange / refresh ──────────────────────────────────────────────────

async def exchange_code(code: str) -> dict:
    """
    Échange un code d'autorisation contre des tokens.
    Retourne le payload Strava complet (access_token, refresh_token, expires_at, athlete, …).
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()


async def refresh_tokens(refresh_token: str) -> dict:
    """
    Rafraîchit les tokens expirés.
    Retourne un dict avec access_token, refresh_token, expires_at.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
