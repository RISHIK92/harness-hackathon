# Payments & Settlement

A booking is settled once its status reaches `completed`. Settlement:

1. Recalculates the commission rate at settlement time (see
   [Pricing & Commission](./04-pricing-and-commission.md)).
2. Deducts commission from the fare to produce the payout.
3. Queues the payout for the next payment run (daily, 09:00 UTC).

Attempting to settle a booking that isn't `completed` returns a `409` —
this most often happens when a customer double-submits the "mark complete"
action in the dashboard, or a webhook consumer settles a booking on receipt
of `booking.completed` while a retried duplicate delivery is also in flight.
