# Webhooks

Meridian can notify your endpoint of booking and payment events in
real time.

## Setting up

1. Go to **Settings → Integrations → Webhooks**.
2. Enter your endpoint URL and generate a signing secret.
3. Send a test ping to verify your endpoint validates the signature
   correctly (see [Signature Verification](./06-signature-verification.md)).

## Events

- `booking.created`
- `booking.completed`
- `booking.cancelled`
- `payment.settled`

## Delivery and retries

If your endpoint doesn't return a `2xx` response, Meridian retries the
delivery with exponential backoff, up to **5 retries over a 24-hour
window**, before marking the delivery as permanently failed. Failed
deliveries appear in **Settings → Integrations → Webhooks → Delivery Log**
along with the response code your endpoint returned.

We recommend responding within 3 seconds and returning `200` before doing
any slow processing — queue the event internally and process it async.
