"""HTTP client for intervals.icu (spec 002 FR-001, FR-002, FR-006, FR-023).

Authentication is basic auth with the literal username "API_KEY" and the athlete's
personal key as the password (research R1, confirmed empirically). No OAuth, no
registered application, no callback — this is what makes the self-hosted story credible
(spec 001).

Error classification verified empirically against the live API rather than assumed:
  - 401 / 403  -> CredentialRejectedError (bad key, or key lacks access to the athlete)
  - 429        -> RateLimitedError
  - network error, timeout, 5xx -> TransientError

No X-RateLimit-* headers were observed on a normal response, so this client does not
depend on them being present — classification is by status code only.
"""
from __future__ import annotations

import httpx

from app.providers.intervals.errors import (
    CredentialRejectedError,
    RateLimitedError,
    TransientError,
)

_BASE_URL = "https://intervals.icu/api/v1"
_TIMEOUT_S = 10.0


class IntervalsClient:
    """Thin wrapper over the intervals.icu REST API. No caching, no retry logic here —
    retry policy belongs to the poller (FR-013), not the client."""

    def __init__(self, api_key: str, *, athlete_id: str = "0"):
        self._api_key = api_key
        self._athlete_id = athlete_id

    async def _get(self, path: str, *, params: dict | None = None) -> dict | list:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            try:
                response = await client.get(
                    f"{_BASE_URL}{path}",
                    auth=("API_KEY", self._api_key),
                    params=params,
                )
            except httpx.TimeoutException as exc:
                raise TransientError(f"Timed out calling {path}") from exc
            except httpx.TransportError as exc:
                raise TransientError(f"Network error calling {path}: {exc}") from exc

        if response.status_code in (401, 403):
            raise CredentialRejectedError(
                f"intervals.icu rejected the credential ({response.status_code}): "
                f"{response.text}"
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitedError(
                "intervals.icu rate limit exceeded",
                retry_after_seconds=float(retry_after) if retry_after else None,
            )
        if response.status_code >= 500:
            raise TransientError(
                f"intervals.icu server error ({response.status_code}) calling {path}"
            )
        response.raise_for_status()  # any other 4xx is a real bug, not a handled case
        return response.json()

    async def get_athlete(self) -> dict:
        """Verify the credential and identify the bound athlete (FR-002).

        Uses athlete id "0" by default, which resolves to whichever athlete the key
        belongs to (research R2) — no athlete id needs to be configured.
        """
        result = await self._get(f"/athlete/{self._athlete_id}")
        assert isinstance(result, dict)
        return result

    async def list_activities(self, *, oldest: str, newest: str) -> list[dict]:
        """List activities in a date window (FR-006). Dates are yyyy-MM-dd."""
        result = await self._get(
            f"/athlete/{self._athlete_id}/activities",
            params={"oldest": oldest, "newest": newest},
        )
        assert isinstance(result, list)
        return result

    async def get_activity(self, activity_id: str, *, with_intervals: bool = False) -> dict:
        """Fetch one activity in full, optionally with detected intervals
        (icu_intervals) for intervals_consistency_index (research R9a)."""
        params = {"intervals": "true"} if with_intervals else None
        result = await self._get(f"/activity/{activity_id}", params=params)
        assert isinstance(result, dict)
        return result

    async def get_activity_streams(self, activity_id: str, *, types: list[str]) -> dict:
        """Fetch raw time-series streams for one activity."""
        result = await self._get(
            f"/activity/{activity_id}/streams",
            params={"types": ",".join(types)},
        )
        assert isinstance(result, dict)
        return result

    async def list_wellness(self, *, oldest: str, newest: str) -> list[dict]:
        """List wellness records in a date window (FR-023). Dates are yyyy-MM-dd."""
        result = await self._get(
            f"/athlete/{self._athlete_id}/wellness",
            params={"oldest": oldest, "newest": newest},
        )
        assert isinstance(result, list)
        return result
