from __future__ import annotations

from fastapi import FastAPI

from app.routers import accounts, bookings, payments, webhooks

app = FastAPI(title="Meridian API")

app.include_router(bookings.router, prefix="/api/bookings", tags=["bookings"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
