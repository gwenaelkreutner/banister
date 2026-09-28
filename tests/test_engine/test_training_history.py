from datetime import date, timedelta
from types import SimpleNamespace as Item

from app.engine.training_history import completed_training_items, daily_training_tss
from app.engine.weekly_snapshot import compute_weekly_snapshot

TODAY = date(2026, 9, 28)


def test_free_rides_contribute_to_weekly_load_and_previous_weeks():
    items = [
        Item(logged_date=TODAY - timedelta(days=1), status="unplanned", tss_actual=120),
        Item(logged_date=TODAY - timedelta(days=1), status="unplanned", tss_actual=80),
        Item(logged_date=TODAY - timedelta(days=10), status="unplanned", tss_actual=120),
    ]
    snap = compute_weekly_snapshot(items, TODAY)
    assert snap.tss_7d == 200
    assert snap.sessions_done_7d == 2
    assert snap.tss_6w_avg == 20
    assert snap.monotony_index is not None


def test_merge_prefers_log_without_collapsing_distinct_rides_or_sources():
    log = Item(logged_date=TODAY, status="unplanned", tss_actual=80,
               source="intervals_icu", source_activity_id="ride-a")
    duplicate = Item(activity_date=TODAY, tss=90,
                     source="intervals_icu", source_activity_id="ride-a")
    other = Item(activity_date=TODAY, tss=30,
                 source="intervals_icu", source_activity_id="ride-b")
    other_source = Item(activity_date=TODAY, tss=10,
                        source="other", source_activity_id="ride-a")
    unidentified = [Item(activity_date=TODAY, tss=5), Item(activity_date=TODAY, tss=7)]
    items = [duplicate, log, other, other_source, *unidentified]
    assert completed_training_items(items) == [log, other, other_source, *unidentified]
    assert daily_training_tss(items, start=TODAY, end=TODAY) == {TODAY: 132}
    assert compute_weekly_snapshot(items, TODAY).tss_7d == 132


def test_daily_window_excludes_skipped_future_and_older_rides():
    items = [
        Item(logged_date=TODAY, status="skipped", tss_actual=999),
        Item(activity_date=TODAY + timedelta(days=1), tss=999),
        Item(activity_date=TODAY - timedelta(days=2), tss=999),
        Item(activity_date=TODAY - timedelta(days=1), tss=60),
        Item(activity_date=TODAY, tss=40),
        Item(activity_date=TODAY, tss=None),
    ]
    assert daily_training_tss(items, start=TODAY - timedelta(days=1), end=TODAY) == {
        TODAY - timedelta(days=1): 60, TODAY: 40,
    }


def test_missing_tss_is_not_estimated_and_zero_is_preserved():
    assert daily_training_tss([Item(activity_date=TODAY, tss=None)], start=TODAY, end=TODAY) == {}
    assert daily_training_tss([Item(activity_date=TODAY, tss=0)], start=TODAY, end=TODAY) == {
        TODAY: 0,
    }
