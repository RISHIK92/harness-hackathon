from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.webhooks import SignatureVerificationError, handle_inbound_verification

router = APIRouter()


@router.post("/{account_id}/verify")
async def verify_webhook_config(
    account_id: str,
    request: Request,
    x_meridian_signature: str = Header(...),
):
    account = _load_account(account_id)
    payload = await request.body()
    try:
        handle_inbound_verification(account, payload, x_meridian_signature)
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"status": "verified"}


def _load_account(account_id: str):  # pragma: no cover
    raise NotImplementedError
