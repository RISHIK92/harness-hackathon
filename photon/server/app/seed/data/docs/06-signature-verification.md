# Signature Verification

Every webhook delivery includes an `X-Meridian-Signature` header:

```
X-Meridian-Signature: sha256=<hex digest>
```

The digest is an HMAC-SHA256 of the raw request body, keyed with your
webhook signing secret (found in **Settings → Integrations → Webhooks →
Signing**).

## Verifying in your endpoint

```python
import hmac, hashlib

def verify(payload: bytes, signature_header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

## Rotating your secret

You can rotate your signing secret at any time from **Settings →
Integrations → Webhooks → Signing**. **Rotating immediately invalidates
the old secret** — update your endpoint's stored secret *before* or *at
the same moment* you rotate in Meridian, or deliveries will start failing
signature verification (Meridian returns a `401` to your test-ping
endpoint, and marks live deliveries failed) until you update it. There is
no overlap/grace period between old and new secrets today.
