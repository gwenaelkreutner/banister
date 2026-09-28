"""Completed training, shared by workload calculations and coaching read models."""

from datetime import date


def item_date(item) -> date | None:
    return getattr(item, "logged_date", None) or getattr(item, "activity_date", None)


def item_tss(item) -> float | None:
    value = getattr(item, "tss_actual", None)
    return value if value is not None else getattr(item, "tss", None)


def completed_training_items(
    items: list, *, start: date | None = None, end: date | None = None,
) -> list:
    """Prefer completed logs over imported copies; never merge by date alone.

    Missing external IDs remain independent. Objects without a status are historical
    activities (or the lightweight equivalents used by the pure engine).
    """
    result = []
    seen = set()
    for item in sorted(items, key=lambda it: not hasattr(it, "logged_date")):
        if getattr(item, "status", None) not in (None, "done", "unplanned"):
            continue
        day = item_date(item)
        if day is None or (start is not None and day < start) or (end is not None and day > end):
            continue
        source_id = getattr(item, "source_activity_id", None)
        if source_id:
            key = (getattr(item, "source", None) or "intervals_icu", source_id)
            if key in seen:
                continue
            seen.add(key)
        result.append(item)
    return result


def daily_training_tss(items: list, *, start: date, end: date) -> dict[date, float]:
    """Sum known source loads per day; missing TSS is never estimated."""
    totals: dict[date, float] = {}
    for item in completed_training_items(items, start=start, end=end):
        tss = item_tss(item)
        if tss is not None:
            day = item_date(item)
            totals[day] = totals.get(day, 0.0) + tss
    return totals
