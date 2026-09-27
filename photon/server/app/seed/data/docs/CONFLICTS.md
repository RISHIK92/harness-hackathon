# Known intentional conflicts (seed corpus)

This file documents deliberate contradictions planted in the Meridian seed
corpus for demo scenario S3 (calibrated abstention). Do not "fix" these by
editing docs or code to agree — the conflict is the point.

## Webhook retry policy

- **Docs claim** (`05-webhooks.md`): "up to 5 retries over a 24-hour window"
- **Code does** (`repo/app/webhooks.py`, `RETRY_BACKOFF_SECONDS`): 3 retries
  at 30s / 120s / 600s offsets — 4 total attempts, all completed within
  ~12.5 minutes of the first failure.

An agent asked "what's Meridian's webhook retry policy?" should detect
that docs and code disagree, cite both, and offer to route to the owner —
not silently pick one.
