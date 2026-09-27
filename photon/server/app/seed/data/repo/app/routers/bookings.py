from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.models import Account
from app.services.booking_service import BookingRejectedError, create_booking

router = APIRouter()


@router.post("")
async def create_booking_endpoint(account_id: str, city: str, when: datetime):
    account = _load_account(account_id)
    try:
        booking = create_booking(account, city, when)
    except BookingRejectedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return booking


def _load_account(account_id: str) -> Account:  # pragma: no cover
    raise NotImplementedError
