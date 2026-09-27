# Troubleshooting

## My webhooks stopped delivering

Most common cause: your endpoint's stored signing secret is out of date
with what's configured in Meridian. Check **Settings → Integrations →
Webhooks → Delivery Log** for the response code on recent attempts — a
`401` there means signature verification failed on your side, which
almost always means the secret you're signing with doesn't match the one
currently configured in Meridian (e.g. after a rotation). See
[Signature Verification](./06-signature-verification.md).

## My booking was rejected with a 422

Check that the requested time falls within local service hours
(06:00–23:00) for the booking's city.

## Settlement returned a 409

The booking isn't in `completed` status yet, or has already been settled.
See [Payments & Settlement](./09-payments-and-settlement.md).

## I think my commission rate is wrong

Rates depend on city, account tier, and any active partner agreement for
that city. Contact Account Management with the booking ID if a rate looks
incorrect — don't assume it's a bug before checking your tier and city.
