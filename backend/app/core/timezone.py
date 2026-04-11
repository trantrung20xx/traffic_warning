from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
VIETNAM_TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


def ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_vietnam_datetime(value: datetime) -> datetime:
    return ensure_utc_datetime(value).astimezone(VIETNAM_TIMEZONE)


def to_vietnam_isoformat(value: datetime) -> str:
    return to_vietnam_datetime(value).replace(microsecond=0).isoformat()
