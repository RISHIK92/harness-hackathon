from __future__ import annotations

from decimal import Decimal

from app.models import Account, Booking
from app.pricing import calculate_commission


class PaymentError(Exception):
    pass


def settle_booking(booking: Booking, account: Account) -> dict:
    if booking.status != "completed":
        raise PaymentError(f"cannot settle booking {booking.id} with status={booking.status}")

    commission = calculate_commission(booking, account)
    payout = (booking.fare - commission).quantize(Decimal("0.01"))

    return {
        "booking_id": booking.id,
        "fare": str(booking.fare),
        "commission": str(commission),
        "payout": str(payout),
    }
