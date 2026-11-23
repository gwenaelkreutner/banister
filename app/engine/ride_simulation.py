"""Form projection for a ride the athlete is considering — pure, no I/O.

Same EMA as `atl_ctl` (τ = 7 / 42 days, one step per calendar day): starting from the
source's last CTL/ATL, every day up to the ride decays, planned sessions with a known
TSS are added on their date, then the ride's TSS on its date. Values are end-of-day,
the convention intervals.icu uses for a day's CTL/ATL.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

_K_ATL = 1 - math.exp(-1 / 7)
_K_CTL = 1 - math.exp(-1 / 42)


@dataclass(frozen=True)
class FormPoint:
    date: date
    ctl: float
    atl: float

    @property
    def tsb(self) -> float:
        return self.ctl - self.atl


@dataclass(frozen=True)
class RideProjection:
    day_before: FormPoint  # end of the day before the ride: the form you start it with
    after: FormPoint  # end of the ride day
    ctl_week_before: float | None  # CTL 7 days before the ride day, for the ramp


def project_ride(
    *,
    ctl: float,
    atl: float,
    as_of: date,
    ride_date: date,
    ride_tss: float,
    planned: dict[date, float],
    history_ctl: dict[date, float] | None = None,
) -> RideProjection:
    """`planned` maps dates strictly after `as_of` to known TSS (other days count as rest).
    `history_ctl` gives measured CTL for days up to `as_of`, used for the 7-day ramp."""
    if ride_date <= as_of:
        raise ValueError("the ride must be after the last known form day")
    series = dict(history_ctl or {})
    series[as_of] = ctl
    day, day_before = as_of, None
    while day < ride_date:
        day += timedelta(days=1)
        tss = ride_tss if day == ride_date else planned.get(day, 0.0)
        if day == ride_date:
            day_before = FormPoint(day - timedelta(days=1), ctl, atl)
        ctl = ctl + (tss - ctl) * _K_CTL
        atl = atl + (tss - atl) * _K_ATL
        series[day] = ctl
    return RideProjection(
        day_before=day_before,
        after=FormPoint(ride_date, ctl, atl),
        ctl_week_before=series.get(ride_date - timedelta(days=7)),
    )
