from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.strava.analysis_models import RawActivity, RawActivityStreams, SportType

logger = logging.getLogger(__name__)

_STRAVA_API_BASE = "https://www.strava.com/api/v3"
_TIER3_STREAM_KEYS = ["time", "watts", "heartrate", "velocity_smooth", "grade_smooth", "moving", "altitude"]
_TIER2_STREAM_KEYS = ["time", "watts", "heartrate"]


class StravaFetchError(RuntimeError):
    pass


class StravaActivityFetcher:
    """Service d'ingestion Strava (sans logique métier)."""

    def __init__(self, *, timeout_s: float = 10.0):
        self.timeout_s = timeout_s

    async def fetch_raw_activity(
        self,
        access_token: str,
        activity_id: int,
        *,
        is_planned_session: bool = False,
    ) -> RawActivity:
        payload = await self._get_activity(access_token, activity_id)

        sport_type = self._map_sport_type(payload.get("sport_type") or payload.get("type"))
        moving_time_s = payload.get("moving_time")
        has_power = bool(payload.get("device_watts"))
        has_heartrate = payload.get("has_heartrate") or bool(payload.get("average_heartrate"))
        has_gps = bool(payload.get("start_latlng"))
        is_manual = bool(payload.get("manual"))
        race_or_test = self._is_race_or_test(payload)

        raw = RawActivity(
            activity_id=str(payload.get("id", activity_id)),
            sport_type=sport_type,
            strava_sport_type=payload.get("sport_type") or payload.get("type"),
            name=payload.get("name"),
            start_datetime=datetime.fromisoformat(payload["start_date"].replace("Z", "+00:00")),
            duration_s=int(payload.get("elapsed_time") or 0),
            moving_time_s=moving_time_s,
            distance_m=payload.get("distance"),
            avg_power=payload.get("average_watts"),
            weighted_avg_power=payload.get("weighted_average_watts"),
            max_power=payload.get("max_watts"),
            avg_hr=payload.get("average_heartrate"),
            max_hr=payload.get("max_heartrate"),
            avg_speed_m_s=payload.get("average_speed"),
            kilojoules=payload.get("kilojoules"),
            suffer_score=payload.get("suffer_score"),
            has_power=has_power,
            has_heartrate=bool(has_heartrate),
            has_gps=has_gps,
            is_manual=is_manual,
            race_or_test=race_or_test,
            is_planned_session=is_planned_session,
            average_temp=payload.get("average_temp"),
            total_elevation_gain=payload.get("total_elevation_gain"),
            athlete_count=int(payload.get("athlete_count") or 1),
        )

        stream_keys = self._select_stream_keys(raw)
        if stream_keys:
            raw.streams = await self._get_activity_streams(access_token, activity_id, stream_keys)

        return raw

    def _select_stream_keys(self, raw: RawActivity) -> list[str]:
        # Activité manuelle : pas de streams disponibles sur Strava
        if raw.is_manual:
            return []

        # Tier 3
        if raw.is_planned_session or raw.race_or_test:
            return _TIER3_STREAM_KEYS

        # Tier 2
        if (
            raw.sport_type in {"ride", "run"}
            and (raw.moving_time_s or 0) > 1200
            and (raw.has_power or raw.has_heartrate)
        ):
            return _TIER2_STREAM_KEYS

        # Tier 1: aucun stream
        return []

    async def ensure_tier3_streams(self, access_token: str, activity_id: int, raw: RawActivity) -> RawActivity:
        """Recharge les streams complets (Tier 3) si nécessaire."""
        required = set(_TIER3_STREAM_KEYS)
        if raw.streams is not None:
            existing = {
                key
                for key, values in raw.streams.model_dump().items()
                if isinstance(values, list) and values
            }
            if required.issubset(existing):
                return raw

        raw.streams = await self._get_activity_streams(access_token, activity_id, _TIER3_STREAM_KEYS)
        return raw

    async def _get_activity(self, access_token: str, activity_id: int) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_STRAVA_API_BASE}/activities/{activity_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self.timeout_s,
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StravaFetchError(f"Impossible de récupérer l'activité {activity_id}") from exc
        return resp.json()

    async def _get_activity_streams(
        self,
        access_token: str,
        activity_id: int,
        stream_keys: list[str],
    ) -> RawActivityStreams:
        params = {
            "keys": ",".join(stream_keys),
            "key_by_type": "true",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_STRAVA_API_BASE}/activities/{activity_id}/streams",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                timeout=self.timeout_s,
            )
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise StravaFetchError(f"Impossible de récupérer les streams de {activity_id}") from exc

        payload = resp.json() or {}

        def vals(key: str):
            return payload.get(key, {}).get("data", []) if isinstance(payload.get(key), dict) else []

        return RawActivityStreams(
            time=vals("time"),
            watts=vals("watts"),
            heartrate=vals("heartrate"),
            velocity_smooth=vals("velocity_smooth"),
            grade_smooth=vals("grade_smooth"),
            moving=vals("moving"),
            altitude=vals("altitude"),
        )

    @staticmethod
    def _is_race_or_test(activity: dict) -> bool:
        # Les activités manuelles n'ont pas de capteurs — impossible de valider une course
        if activity.get("manual"):
            return False
        workout_type = activity.get("workout_type")
        name = (activity.get("name") or "").lower()
        if workout_type in {1, 11}:
            return True
        return "race" in name or "test" in name

    @staticmethod
    def _map_sport_type(strava_sport_type: str | None) -> SportType:
        mapping = {
            "Ride": "ride",
            "VirtualRide": "ride",
            "MountainBikeRide": "ride",
            "GravelRide": "ride",
            "EBikeRide": "ride",
            "Run": "run",
            "TrailRun": "run",
            "Swim": "swim",
        }
        return mapping.get(strava_sport_type or "", "other")
