"""Read everything intervals.icu knows about the athlete, with provenance (spec 007 US1).

`GET /athlete/{id}` carries the threshold figures (inside `sportSettings[]`, per sport —
NOT the top-level `icu_ftp`, which is null on real accounts), plus weight, sex, date of
birth, resting HR and locale at the top level (research R1).

This module is a pure mapping — one client call, no other I/O. Setup shows the result
for confirmation (FR-002); every field carries where it came from (FR-005) and a field
the source lacks is returned **absent**, never defaulted (FR-008).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.providers.intervals.client import IntervalsClient

# sportSettings[].types values that mean "this is the cycling entry".
_CYCLING_TYPES = frozenset(
    {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "TrackRide", "Cyclocross"}
)


@dataclass(frozen=True)
class ReadValue:
    """One attribute read from the source."""

    value: float | int | str | None
    origin: str                 # stable source code, rendered by the caller
    as_of: date | None = None    # when the source dates it, if it does
    note: str | None = None      # stable note code, rendered by the caller

    @property
    def present(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class ReadProfile:
    """What the source could supply. Anything the source lacks is a ReadValue with
    value=None — the setup flow asks for those rather than defaulting (FR-008)."""

    ftp: ReadValue
    lthr: ReadValue
    max_hr: ReadValue
    resting_hr: ReadValue
    weight_kg: ReadValue
    sex: ReadValue
    age: ReadValue
    date_of_birth: ReadValue
    locale: ReadValue
    has_power_meter: bool = False    # True iff a cycling sportSettings entry has an ftp
    fields: list[ReadValue] = field(default_factory=list, repr=False)

    @property
    def coaching_mode(self) -> str:
        """FR-009: no measured cycling FTP → the coach runs on heart rate."""
        return "power" if self.ftp.present else "hr"


def _cycling_sport_settings(athlete: dict) -> dict | None:
    for s in athlete.get("sportSettings") or []:
        if _CYCLING_TYPES.intersection(s.get("types") or []):
            return s
    return None


def _age_from_dob(dob_raw: str | None) -> tuple[int | None, date | None]:
    if not dob_raw:
        return None, None
    try:
        dob = date.fromisoformat(dob_raw[:10])
    except ValueError:
        return None, None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age, dob


def map_athlete_profile(athlete: dict) -> ReadProfile:
    """Pure mapping — the testable half. `athlete` is the `GET /athlete/{id}` payload."""
    sport = _cycling_sport_settings(athlete) or {}
    _src = "source"
    _src_sport = "sport_settings"

    ftp_raw = sport.get("ftp")
    ftp = ReadValue(ftp_raw, _src_sport) if ftp_raw else ReadValue(None, _src_sport)
    lthr = ReadValue(sport.get("lthr"), _src_sport)
    max_hr = ReadValue(sport.get("max_hr"), _src_sport)

    resting_hr = ReadValue(
        athlete.get("icu_resting_hr"),
        _src,
        note="resting_hr_profile",
    )
    weight = ReadValue(athlete.get("icu_weight"), _src)
    sex = ReadValue(athlete.get("sex"), _src)

    age_val, dob = _age_from_dob(athlete.get("icu_date_of_birth"))
    age = ReadValue(age_val, "birth_date")
    dob_rv = ReadValue(dob.isoformat() if dob else None, _src)
    locale = ReadValue(athlete.get("locale"), _src)

    profile = ReadProfile(
        ftp=ftp,
        lthr=lthr,
        max_hr=max_hr,
        resting_hr=resting_hr,
        weight_kg=weight,
        sex=sex,
        age=age,
        date_of_birth=dob_rv,
        locale=locale,
        has_power_meter=bool(ftp_raw),
    )
    object.__setattr__(
        profile,
        "fields",
        [ftp, lthr, max_hr, resting_hr, weight, sex, age],
    )
    return profile


async def read_athlete_profile(client: IntervalsClient) -> ReadProfile:
    """One call to the source, then `map_athlete_profile`."""
    athlete = await client.get_athlete()
    return map_athlete_profile(athlete)


def stamp() -> str:
    """ISO datetime for `profile.read_from_source_at` — recorded when setup reads."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
