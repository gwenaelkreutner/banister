import pytest

from app.strava.analysis_models import RawActivity, RawActivityStreams
from app.strava.fetcher import StravaActivityFetcher


def _raw(**overrides) -> RawActivity:
    base = RawActivity(
        activity_id="1",
        sport_type="ride",
        start_datetime="2026-01-01T10:00:00+00:00",
        duration_s=3600,
        moving_time_s=3500,
        distance_m=30000,
        has_power=True,
        has_heartrate=True,
        has_gps=True,
    )
    return base.model_copy(update=overrides)


def test_select_stream_keys_tier3_when_planned():
    fetcher = StravaActivityFetcher()
    keys = fetcher._select_stream_keys(_raw(is_planned_session=True))
    assert "grade_smooth" in keys
    assert len(keys) == 7


def test_select_stream_keys_tier3_when_race_or_test():
    fetcher = StravaActivityFetcher()
    keys = fetcher._select_stream_keys(_raw(race_or_test=True, has_power=False, has_heartrate=False))
    assert "altitude" in keys
    assert len(keys) == 7


def test_select_stream_keys_tier2_standard():
    fetcher = StravaActivityFetcher()
    keys = fetcher._select_stream_keys(_raw(moving_time_s=2000, has_power=False, has_heartrate=True))
    assert keys == ["time", "watts", "heartrate"]


def test_select_stream_keys_tier1_minimum():
    fetcher = StravaActivityFetcher()
    keys = fetcher._select_stream_keys(_raw(moving_time_s=600, has_power=False, has_heartrate=False))
    assert keys == []


@pytest.mark.asyncio
async def test_fetch_raw_activity_calls_streams_only_when_needed(monkeypatch):
    fetcher = StravaActivityFetcher()

    async def fake_get_activity(access_token, activity_id):
        return {
            "id": activity_id,
            "sport_type": "Ride",
            "start_date": "2026-01-01T10:00:00Z",
            "elapsed_time": 3600,
            "moving_time": 3500,
            "distance": 25000.0,
            "average_watts": 220,
            "average_heartrate": 150,
            "start_latlng": [48.8, 2.3],
        }

    called = {"streams": 0}

    async def fake_get_streams(access_token, activity_id, keys):
        called["streams"] += 1
        return None

    monkeypatch.setattr(fetcher, "_get_activity", fake_get_activity)
    monkeypatch.setattr(fetcher, "_get_activity_streams", fake_get_streams)

    await fetcher.fetch_raw_activity("token", 123, is_planned_session=False)
    assert called["streams"] == 1


@pytest.mark.asyncio
async def test_ensure_tier3_streams_upgrades_payload(monkeypatch):
    fetcher = StravaActivityFetcher()
    raw = _raw()

    async def fake_get_streams(access_token, activity_id, keys):
        assert "grade_smooth" in keys
        return RawActivityStreams(time=[0, 1], watts=[100, 110])

    monkeypatch.setattr(fetcher, "_get_activity_streams", fake_get_streams)

    upgraded = await fetcher.ensure_tier3_streams("token", 1, raw)
    assert upgraded is raw


@pytest.mark.asyncio
async def test_fetch_extracts_strava_context_fields(monkeypatch):
    """average_temp, total_elevation_gain et athlete_count sont extraits du payload Strava."""
    fetcher = StravaActivityFetcher()

    async def fake_get_activity(access_token, activity_id):
        return {
            "id": activity_id,
            "sport_type": "Ride",
            "start_date": "2026-01-01T10:00:00Z",
            "elapsed_time": 3600,
            "moving_time": 3500,
            "distance": 40000.0,
            "average_watts": 210,
            "average_heartrate": 145,
            "start_latlng": [48.8, 2.3],
            "average_temp": 16.0,
            "total_elevation_gain": 620.0,
            "athlete_count": 4,
        }

    async def fake_get_streams(access_token, activity_id, keys):
        return RawActivityStreams()

    monkeypatch.setattr(fetcher, "_get_activity", fake_get_activity)
    monkeypatch.setattr(fetcher, "_get_activity_streams", fake_get_streams)

    raw = await fetcher.fetch_raw_activity("token", 999)

    assert raw.average_temp == 16.0
    assert raw.total_elevation_gain == 620.0
    assert raw.athlete_count == 4


@pytest.mark.asyncio
async def test_fetch_athlete_count_defaults_to_1_when_absent(monkeypatch):
    """athlete_count vaut 1 quand Strava ne fournit pas le champ (sortie solo)."""
    fetcher = StravaActivityFetcher()

    async def fake_get_activity(access_token, activity_id):
        return {
            "id": activity_id,
            "sport_type": "Ride",
            "start_date": "2026-01-01T10:00:00Z",
            "elapsed_time": 3600,
            "moving_time": 3500,
            "distance": 30000.0,
            "average_watts": 190,
            "start_latlng": [48.8, 2.3],
            # pas de athlete_count, average_temp, total_elevation_gain
        }

    async def fake_get_streams(access_token, activity_id, keys):
        return RawActivityStreams()

    monkeypatch.setattr(fetcher, "_get_activity", fake_get_activity)
    monkeypatch.setattr(fetcher, "_get_activity_streams", fake_get_streams)

    raw = await fetcher.fetch_raw_activity("token", 1000)

    assert raw.athlete_count == 1
    assert raw.average_temp is None
    assert raw.total_elevation_gain is None
