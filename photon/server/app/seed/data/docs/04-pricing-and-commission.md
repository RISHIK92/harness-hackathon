# Pricing & Commission

Meridian retains a commission on every completed booking. The commission
rate is a multiplier applied to the booking's base fare and depends on:

1. **City** — each city has a base rate, with a per-city surcharge on top
   for a handful of higher-cost markets (Mumbai, Delhi, Singapore, Dubai).
2. **Account tier** — `enterprise` accounts get a flat 5-point reduction
   applied after the city rate. `partner` accounts may have a
   preferential rate in specific cities where Meridian has an active
   reseller agreement.
3. Rates are recalculated at settlement time, not at booking time, so a
   mid-cycle tier change applies to bookings settled after the change.

For the exact numeric rates in effect, use `GET /api/accounts/{id}/rates`
or contact Account Management — we don't publish the full rate table here
since it changes as new partner agreements are signed.
