from decimal import Decimal

import pytest

from app.models import Account, Booking
from app.pricing import calculate_commission, commission_rate


def _account(tier: str) -> Account:
    return Account(id="acct_test", name="Test Co", tier=tier, home_city="Bangalore")


def _booking(city: str, fare: str) -> Booking:
    return Booking(id="bk_test", account_id="acct_test", city=city, fare=Decimal(fare), status="completed")


def test_partner_bangalore_gets_discounted_rate():
    account = _account("partner")
    booking = _booking("Bangalore", "1000.00")
    assert commission_rate(booking, account) == Decimal("0.88")


def test_standard_bangalore_gets_base_rate():
    account = _account("standard")
    booking = _booking("Bangalore", "1000.00")
    assert commission_rate(booking, account) == Decimal("1.00")


def test_partner_other_city_uses_surcharge_table():
    account = _account("partner")
    booking = _booking("Mumbai", "1000.00")
    assert commission_rate(booking, account) == Decimal("1.12")
