"""Tests for IntervalsClient (spec 002 T009).

Uses httpx.MockTransport rather than a new mocking dependency — the client already
depends on httpx, and MockTransport intercepts at the transport layer so the real
request-building code (auth, params, path) still runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.errors import (
    CredentialRejectedError,
    RateLimitedError,
    TransientError,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "intervals"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def patch_transport(monkeypatch):
    """Patch httpx.AsyncClient construction so every client._get() call in a test
    routes through a given MockTransport instead of the network."""

    def _apply(handler):
        real_async_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

    return _apply


class TestGetAthlete:
    async def test_returns_athlete_payload(self, patch_transport):
        athlete = _load("athlete.json")

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/athlete/i000000"
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json=athlete)

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        result = await client.get_athlete()

        assert result["id"] == "i000000"

    async def test_uses_basic_auth_with_api_key_username(self, patch_transport):
        import base64

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["authorization"]
            return httpx.Response(200, json=_load("athlete.json"))

        patch_transport(handler)
        client = IntervalsClient("my-secret-key", athlete_id="0")
        await client.get_athlete()

        scheme, _, encoded = seen["auth"].partition(" ")
        assert scheme == "Basic"
        assert base64.b64decode(encoded).decode() == "API_KEY:my-secret-key"


class TestErrorClassification:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_401_403_raise_credential_rejected(self, patch_transport, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, text="unauthorized")

        patch_transport(handler)
        client = IntervalsClient("bad-key")

        with pytest.raises(CredentialRejectedError):
            await client.get_athlete()

    async def test_429_raises_rate_limited_with_retry_after(self, patch_transport):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "30"})

        patch_transport(handler)
        client = IntervalsClient("test-key")

        with pytest.raises(RateLimitedError) as exc_info:
            await client.get_athlete()

        assert exc_info.value.retry_after_seconds == 30.0

    async def test_429_without_retry_after_header(self, patch_transport):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        patch_transport(handler)
        client = IntervalsClient("test-key")

        with pytest.raises(RateLimitedError) as exc_info:
            await client.get_athlete()

        assert exc_info.value.retry_after_seconds is None

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_5xx_raises_transient(self, patch_transport, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status)

        patch_transport(handler)
        client = IntervalsClient("test-key")

        with pytest.raises(TransientError):
            await client.get_athlete()

    async def test_network_error_raises_transient(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        real_async_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)
        client = IntervalsClient("test-key")

        with pytest.raises(TransientError):
            await client.get_athlete()

    async def test_timeout_raises_transient(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        real_async_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)
        client = IntervalsClient("test-key")

        with pytest.raises(TransientError):
            await client.get_athlete()

    async def test_other_4xx_propagates_as_http_status_error(self, patch_transport):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        patch_transport(handler)
        client = IntervalsClient("test-key")

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_athlete()


class TestListActivities:
    async def test_passes_date_window_params(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            assert request.url.path == "/api/v1/athlete/i000000/activities"
            return httpx.Response(200, json=[_load("activity_full.json")])

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        result = await client.list_activities(oldest="2026-01-01", newest="2026-01-31")

        assert seen["params"] == {"oldest": "2026-01-01", "newest": "2026-01-31"}
        assert len(result) == 1


class TestGetActivity:
    async def test_with_intervals_flag_sets_query_param(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=_load("activity_with_intervals.json"))

        patch_transport(handler)
        client = IntervalsClient("test-key")

        await client.get_activity("a123", with_intervals=True)

        assert seen["params"] == {"intervals": "true"}

    async def test_without_intervals_flag_sends_no_params(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json=_load("activity_full.json"))

        patch_transport(handler)
        client = IntervalsClient("test-key")

        await client.get_activity("a123")

        assert seen["params"] == {}

    async def test_returns_no_sensors_activity_verbatim(self, patch_transport):
        """The no-sensor fixture is the critical case: null fields must survive the
        round trip unchanged, since the mapper (T011) depends on that (FR-020)."""
        fixture = _load("activity_no_sensors.json")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fixture)

        patch_transport(handler)
        client = IntervalsClient("test-key")

        result = await client.get_activity("a456")

        assert result["icu_training_load"] is None
        assert result["device_watts"] is None


class TestUpdateActivityRpe:
    async def test_puts_only_icu_rpe_on_existing_activity(self, patch_transport):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PUT"
            assert request.url.path == "/api/v1/activity/i123"
            assert json.loads(request.content) == {"icu_rpe": 8}
            return httpx.Response(200, json={"id": "i123", "icu_rpe": 8})

        patch_transport(handler)

        result = await IntervalsClient("test-key").update_activity_rpe("i123", 8)

        assert result["icu_rpe"] == 8


class TestListWellness:
    async def test_passes_date_window_params(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            assert request.url.path == "/api/v1/athlete/i000000/wellness"
            return httpx.Response(200, json=_load("wellness_range.json"))

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        result = await client.list_wellness(oldest="2026-08-01", newest="2026-08-08")

        assert seen["params"] == {"oldest": "2026-08-01", "newest": "2026-08-08"}
        assert isinstance(result, list)


class TestGetActivityStreams:
    """The real API returns a LIST of {type, data} objects, not a dict — verified
    against the live API 2026-09-21 (this method's old `assert isinstance(result, dict)`
    would have raised on every real call, never having been exercised before)."""

    async def test_returns_a_list_not_a_dict(self, patch_transport):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/activity/i188613791/streams"
            assert dict(request.url.params) == {"types": "watts,heartrate,time"}
            return httpx.Response(
                200,
                json=[
                    {"type": "time", "data": [0, 1, 2]},
                    {"type": "watts", "data": [100, 110, 120]},
                    {"type": "heartrate", "data": [140, 141, 142]},
                ],
            )

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        result = await client.get_activity_streams(
            "i188613791", types=["watts", "heartrate", "time"]
        )

        assert isinstance(result, list)
        assert {s["type"] for s in result} == {"time", "watts", "heartrate"}


class TestGetPowerCurves:
    async def test_power_curves_passes_type_and_joined_windows(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            assert request.url.path == "/api/v1/athlete/i000000/power-curves"
            return httpx.Response(200, json={"list": [], "activities": []})

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        result = await client.get_power_curves(
            curve_type="power",
            windows=[("2026-08-25", "2026-09-21"), ("2026-07-28", "2026-08-24")],
            activity_type="Ride",
        )

        assert seen["params"] == {
            "curves": "r.2026-08-25.2026-09-21,r.2026-07-28.2026-08-24",
            "type": "Ride",
        }
        assert result == {"list": [], "activities": []}

    async def test_hr_curves_endpoint_and_no_type_param(self, patch_transport):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"list": [], "activities": []})

        patch_transport(handler)
        client = IntervalsClient("test-key", athlete_id="i000000")

        await client.get_power_curves(
            curve_type="hr", windows=[("2026-08-25", "2026-09-21")]
        )

        assert seen["path"] == "/api/v1/athlete/i000000/hr-curves"
        assert "type" not in seen["params"]
