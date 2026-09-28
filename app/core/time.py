"""Calendar day shared by synchronization and the coach's daily context."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")


def paris_today() -> date:
    return datetime.now(PARIS_TZ).date()
