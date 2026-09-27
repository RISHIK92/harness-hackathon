from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.auth import decode_session_token

router = APIRouter()


@router.get("/{account_id}/webhook-config")
async def get_webhook_config(account_id: str):
    account = _load_account(account_id)
    return {
        "webhook_url": account.webhook_url,
        "webhook_enabled": account.webhook_enabled,
        "signing_secret_last4": account.webhook_signing_secret[-4:]
        if account.webhook_signing_secret
        else None,
    }


def _load_account(account_id: str):  # pragma: no cover
    raise NotImplementedError
