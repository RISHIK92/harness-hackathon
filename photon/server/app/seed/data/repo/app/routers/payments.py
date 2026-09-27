from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.payments import PaymentError, settle_booking

router = APIRouter()


@router.post("/{booking_id}/settle")
async def settle_booking_endpoint(booking_id: str):
    booking, account = _load_booking_and_account(booking_id)
    try:
        result = settle_booking(booking, account)
    except PaymentError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result


def _load_booking_and_account(booking_id: str):  # pragma: no cover
    raise NotImplementedError
