from __future__ import annotations

from decimal import Decimal

from app.models import Account, Booking

BASE_RATE = Decimal("1.00")

CITY_SURCHARGES = {
    "Mumbai": Decimal("0.12"),
    "Delhi": Decimal("0.10"),
    "Singapore": Decimal("0.18"),
    "Dubai": Decimal("0.15"),
}

PARTNER_CITY_RATES = {
    "Bangalore": Decimal("0.88"),
}


def commission_rate(booking: Booking, account: Account) -> Decimal:
    """Return the commission multiplier applied to a booking's base fare."""
    if booking.city == "Bangalore" and account.tier == "partner":
        rate = PARTNER_CITY_RATES[booking.city]
    else:
        surcharge = CITY_SURCHARGES.get(booking.city, Decimal("0"))
        rate = BASE_RATE + surcharge

    if account.tier == "enterprise":
        rate -= Decimal("0.05")

    return rate


def calculate_commission(booking: Booking, account: Account) -> Decimal:
    rate = commission_rate(booking, account)
    return (booking.fare * rate).quantize(Decimal("0.01"))
