import math
from datetime import date, timedelta

import pytest

from app.engine.ride_simulation import project_ride

D0 = date(2026, 11, 20)


def test_rest_days_then_ride_follow_the_engine_ema():
    ride = D0 + timedelta(days=3)
    p = project_ride(ctl=50, atl=60, as_of=D0, ride_date=ride, ride_tss=200, planned={})
    k7, k42 = math.exp(-1 / 7), math.exp(-1 / 42)
    assert p.day_before.atl == pytest.approx(60 * k7**2)
    assert p.after.atl == pytest.approx(60 * k7**3 + 200 * (1 - k7))
    assert p.after.ctl == pytest.approx(50 * k42**3 + 200 * (1 - k42))


def test_planned_sessions_count_and_ramp_uses_history():
    planned = {D0 + timedelta(days=1): 100.0}
    hist = {D0 - timedelta(days=4): 45.0}
    with_plan = project_ride(ctl=50, atl=50, as_of=D0, ride_date=D0 + timedelta(days=3),
                             ride_tss=150, planned=planned, history_ctl=hist)
    without = project_ride(ctl=50, atl=50, as_of=D0, ride_date=D0 + timedelta(days=3),
                           ride_tss=150, planned={}, history_ctl=hist)
    assert with_plan.after.atl > without.after.atl
    assert with_plan.ctl_week_before == 45.0


def test_ride_must_be_after_the_known_day():
    with pytest.raises(ValueError):
        project_ride(ctl=1, atl=1, as_of=D0, ride_date=D0, ride_tss=10, planned={})
