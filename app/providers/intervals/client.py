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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | list | None = None,
    ) -> dict | list | None:
        """One request, one shared error classification (research R6). Write verbs
        (_post/_put/_delete) go through here exactly as _get does — the classification
        that FR-005/FR-013 depend on above the client boundary must not fork per method.
        """
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            try:
                response = await client.request(
                    method,
                    f"{_BASE_URL}{path}",
                    auth=("API_KEY", self._api_key),
                    params=params,
                    json=json,
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
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _get(self, path: str, *, params: dict | None = None) -> dict | list:
        result = await self._request("GET", path, params=params)
        assert result is not None
        return result

    async def _post(self, path: str, *, json: dict | list) -> dict | list | None:
        return await self._request("POST", path, json=json)

    async def _put(self, path: str, *, json: dict | list) -> dict | list | None:
        return await self._request("PUT", path, json=json)

    async def _delete(self, path: str) -> None:
        await self._request("DELETE", path)

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

    async def get_activity_streams(self, activity_id: str, *, types: list[str]) -> list[dict]:
        """Fetch raw time-series streams for one activity — a list of `{type, data}`
        objects, one per requested stream that actually has data (verified against the
        real API 2026-09-21: NOT a dict, contrary to what this method's signature
        claimed until then, having never been called anywhere in the app before)."""
        result = await self._get(
            f"/activity/{activity_id}/streams",
            params={"types": ",".join(types)},
        )
        assert isinstance(result, list)
        return result

    async def get_power_curves(
        self,
        *,
        curve_type: str,
        windows: list[tuple[str, str]],
        activity_type: str | None = None,
    ) -> dict:
        """Fetch power (`curve_type="power"`) or HR (`curve_type="hr"`) curves for one
        or more date windows in a single call — `windows` is a list of `(oldest,
        newest)` yyyy-MM-dd pairs, joined as intervals.icu's `r.<oldest>.<newest>` curve
        spec (verified against the real API 2026-09-21). Response shape:
        `{"list": [{"id": "r.<oldest>.<newest>", "secs": [...], "watts": [...], ...}],
        "activities": [...]}` — curves are matched by `id`, not list position (a window
        with zero qualifying activities is simply omitted from `list`, per research).
        `activity_type` filters power curves to one intervals.icu activity type (e.g.
        "Ride", "VirtualRide") — a caller wanting indoor+outdoor merged (like
        `app/engine/power_curve.py::compute_sustainability_profile`) calls this once per
        type and merges the responses; ignored for `curve_type="hr"` (verified: the
        hr-curves endpoint accepts no `type` filter)."""
        endpoint = "power-curves" if curve_type == "power" else "hr-curves"
        params: dict = {
            "curves": ",".join(f"r.{oldest}.{newest}" for oldest, newest in windows)
        }
        if curve_type == "power" and activity_type:
            params["type"] = activity_type
        result = await self._get(f"/athlete/{self._athlete_id}/{endpoint}", params=params)
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

    # ── Calendar events (spec 005 research R1/R2) ─────────────────────────────
    #
    # This is the moment the project stops being read-only. Every verb below is a
    # write path that must be gated on a recorded approval one layer up
    # (app/services/publication.py) — the client itself does not know about consent.

    async def list_events(self, *, oldest: str, newest: str) -> list[dict]:
        """List calendar events (planned workouts) in a date window. Dates are
        yyyy-MM-dd. Used to diff before writing — the API does not upsert (R2)."""
        result = await self._get(
            f"/athlete/{self._athlete_id}/events",
            params={"oldest": oldest, "newest": newest},
        )
        assert isinstance(result, list)
        return result

    async def create_event(self, payload: dict) -> dict:
        """POST a new calendar event. Re-POSTing the same external_id creates a
        duplicate (R2) — the caller is responsible for not doing that."""
        result = await self._post(f"/athlete/{self._athlete_id}/events", json=payload)
        assert isinstance(result, dict)
        return result

    async def update_event(self, event_id: str, payload: dict) -> dict:
        """PUT an existing calendar event by its server-assigned id (R2)."""
        result = await self._put(
            f"/athlete/{self._athlete_id}/events/{event_id}", json=payload
        )
        assert isinstance(result, dict)
        return result

    async def delete_event(self, event_id: str) -> None:
        """DELETE a calendar event by its server-assigned id."""
        await self._delete(f"/athlete/{self._athlete_id}/events/{event_id}")
