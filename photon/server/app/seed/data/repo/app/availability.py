from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Account

SLOT_MINUTES = 30


def next_available_slots(account: Account, count: int = 5) -> list[datetime]:
    now = datetime.utcnow()
    aligned = now - timedelta(
        minutes=now.minute % SLOT_MINUTES, seconds=now.second, microseconds=now.microsecond
    )
    return [aligned + timedelta(minutes=SLOT_MINUTES * (i + 1)) for i in range(count)]


def is_within_service_hours(city: str, when: datetime) -> bool:
    # Meridian operates 06:00-23:00 local; the timezone lookup lives in
    # app.services.rate_service.CITY_TIMEZONES.
    return 6 <= when.hour < 23
