from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from app.models import Account
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Delivery is attempted immediately, then retried at these offsets (seconds)
# if the endpoint doesn't return 2xx. After the last attempt the delivery is
# marked failed and surfaced in the account's webhook health panel.
RETRY_BACKOFF_SECONDS = [30, 120, 600]
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1


class SignatureVerificationError(Exception):
    pass


@dataclass
class WebhookDelivery:
    account_id: str
    event_type: str
    payload: bytes
    attempt: int = 0


def sign_payload(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(expected, signature_header)


def handle_inbound_verification(account: Account, payload: bytes, signature_header: str) -> None:
    """Called on inbound webhook config test pings from the customer's endpoint."""
    if not verify_signature(payload, signature_header, account.webhook_signing_secret):
        logger.warning(
            "webhook.signature_mismatch",
            account_id=account.id,
            endpoint=account.webhook_url,
        )
        raise SignatureVerificationError(
            f"signature mismatch for account={account.id}"
        )


def deliver(delivery: WebhookDelivery, account: Account) -> bool:
    """Attempt one delivery. Returns True on 2xx, False otherwise.

    Retries are scheduled by the caller (Celery beat) using
    RETRY_BACKOFF_SECONDS; this function does not sleep or loop itself.
    """
    signature = sign_payload(delivery.payload, account.webhook_signing_secret)
    response_status = _send(account.webhook_url, delivery.payload, signature)

    if response_status == 401:
        logger.warning(
            "webhook.delivery_unauthorized",
            account_id=account.id,
            attempt=delivery.attempt,
            endpoint=account.webhook_url,
        )
        return False

    if 200 <= response_status < 300:
        return True

    logger.warning(
        "webhook.delivery_failed",
        account_id=account.id,
        status=response_status,
        attempt=delivery.attempt,
    )
    return False


def _send(url: str, payload: bytes, signature: str) -> int:  # pragma: no cover
    # Real HTTP call lives in app.services.notification_service; stubbed here
    # so pricing/webhook logic stays independently testable.
    raise NotImplementedError
