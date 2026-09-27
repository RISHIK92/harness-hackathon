from __future__ import annotations

import httpx

from app.models import Account
from app.utils.logging import get_logger
from app.webhooks import WebhookDelivery, sign_payload

logger = get_logger(__name__)

DELIVERY_TIMEOUT_SECONDS = 5


def send_webhook(delivery: WebhookDelivery, account: Account) -> int:
    """Real HTTP delivery. Called by the Celery task that schedules retries
    using app.webhooks.RETRY_BACKOFF_SECONDS."""
    if not account.webhook_enabled or not account.webhook_url:
        logger.info("webhook.skipped_disabled", account_id=account.id)
        return 0

    signature = sign_payload(delivery.payload, account.webhook_signing_secret)
    try:
        response = httpx.post(
            account.webhook_url,
            content=delivery.payload,
            headers={
                "X-Meridian-Signature": signature,
                "X-Meridian-Event": delivery.event_type,
                "Content-Type": "application/json",
            },
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        return response.status_code
    except httpx.RequestError as exc:
        logger.warning("webhook.request_error", account_id=account.id, error=str(exc))
        return 0
