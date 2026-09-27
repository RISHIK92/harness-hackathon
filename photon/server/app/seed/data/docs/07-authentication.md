# Authentication

Meridian supports two authentication modes:

## Session tokens (dashboard/browser)

Issued on login, a short-lived JWT (12 hour TTL) signed with `HS256`.
Carried as a cookie by the dashboard; not intended for server-to-server use.

## API keys (integrations)

Long-lived keys scoped to `read` or `read_write`, generated per-account
under **Settings → Integrations → API Keys**. Send as:

```
Authorization: Bearer mk_live_...
```

API keys do not expire automatically. Revoke a compromised key immediately
from the same settings page — revocation takes effect within a few seconds.
