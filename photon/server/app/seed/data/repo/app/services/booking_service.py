from __future__ import annotations

from decimal import Decimal

from app.availability import is_within_service_hours
from app.database import get_session
from app.models import Account, Booking
from app.services.rate_service import base_fare_for_city


class BookingRejectedError(Exception):
    pass


def create_booking(account: Account, city: str, when) -> Booking:
    if not is_within_service_hours(city, when):
        raise BookingRejectedError(f"{city} is outside service hours at {when}")

    fare = base_fare_for_city(city)
    with get_session() as session:
        booking = Booking(
            id=_next_booking_id(session),
            account_id=account.id,
            city=city,
            fare=fare,
            status="pending",
        )
        session.add(booking)
    return booking


def _next_booking_id(session) -> str:
    from uuid import uuid4

    return f"bk_{uuid4().hex[:10]}"
