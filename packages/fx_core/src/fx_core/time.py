from datetime import UTC, datetime, timedelta
from enum import Enum


class Horizon(Enum):
    MINUTES_15 = "15m"
    HOUR_1 = "1h"
    HOURS_4 = "4h"
    DAY_1 = "1d"
    DAYS_3 = "3d"

    @property
    def duration(self) -> timedelta:
        return {
            Horizon.MINUTES_15: timedelta(minutes=15),
            Horizon.HOUR_1: timedelta(hours=1),
            Horizon.HOURS_4: timedelta(hours=4),
            Horizon.DAY_1: timedelta(days=1),
            Horizon.DAYS_3: timedelta(days=3),
        }[self]


def require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be UTC")

