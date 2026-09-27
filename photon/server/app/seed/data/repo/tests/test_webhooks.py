import pytest

from app.models import Account
from app.webhooks import SignatureVerificationError, handle_inbound_verification, sign_payload


def _account(secret: str) -> Account:
    return Account(
        id="acct_test",
        name="Test Co",
        tier="standard",
        home_city="Bangalore",
        webhook_url="https://example.com/hooks/meridian",
        webhook_signing_secret=secret,
        webhook_enabled=True,
    )


def test_valid_signature_passes():
    account = _account("whsec_abc123")
    payload = b'{"event":"booking.completed"}'
    sig = sign_payload(payload, account.webhook_signing_secret)
    handle_inbound_verification(account, payload, sig)  # does not raise


def test_stale_secret_fails_verification():
    account = _account("whsec_new")
    payload = b'{"event":"booking.completed"}'
    sig = sign_payload(payload, "whsec_old")  # signed with rotated-out secret
    with pytest.raises(SignatureVerificationError):
        handle_inbound_verification(account, payload, sig)
