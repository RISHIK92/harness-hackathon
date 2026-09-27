from __future__ import annotations

from decimal import Decimal

CITY_TIMEZONES = {
    "Bangalore": "Asia/Kolkata",
    "Mumbai": "Asia/Kolkata",
    "Delhi": "Asia/Kolkata",
    "Singapore": "Asia/Singapore",
    "Dubai": "Asia/Dubai",
}

BASE_FARES = {
    "Bangalore": Decimal("450.00"),
    "Mumbai": Decimal("520.00"),
    "Delhi": Decimal("500.00"),
    "Singapore": Decimal("38.00"),
    "Dubai": Decimal("140.00"),
}


def base_fare_for_city(city: str) -> Decimal:
    return BASE_FARES.get(city, Decimal("400.00"))
