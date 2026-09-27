# Getting Started with Meridian

Meridian is a B2B booking and scheduling platform. This guide covers
connecting your account, creating your first booking, and understanding
how commission is calculated.

## 1. Create an account

Sign up at meridian.dev/signup with your business email. You'll be assigned
an account tier (`standard`, `partner`, or `enterprise`) based on your
onboarding conversation with Sales.

## 2. Generate an API key

Go to **Settings → Integrations → API Keys** and generate a key scoped to
either `read` or `read_write`.

## 3. Create a booking

```
POST /api/bookings
{
  "account_id": "acct_...",
  "city": "Bangalore",
  "when": "2026-09-01T14:30:00Z"
}
```

## 4. Understand commission

Every completed booking is settled through `POST /api/payments/{booking_id}/settle`,
which returns the fare, the commission Meridian retains, and your payout.
Commission rates vary by city and account tier — see
[Pricing & Commission](./04-pricing-and-commission.md).
