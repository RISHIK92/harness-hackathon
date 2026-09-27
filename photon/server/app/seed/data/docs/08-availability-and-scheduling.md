# Availability & Scheduling

Bookings are offered in 30-minute slots. `GET /api/accounts/{id}/availability`
returns the next 5 open slots aligned to the half hour, in the account's
home city timezone.

Bookings can only be created within local service hours (06:00–23:00).
Outside that window the booking API returns a `422` — this is enforced at
booking-creation time, not at settlement, so a booking created just before
23:00 in one timezone but scheduled for a service window past it will still
be rejected.
