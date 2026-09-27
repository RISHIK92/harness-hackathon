from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class AccountTier(str, Enum):
    STANDARD = "standard"
    PARTNER = "partner"
    ENTERPRISE = "enterprise"


@dataclass
class Account:
    id: str
    name: str
    tier: str
    home_city: str
    webhook_url: str | None = None
    webhook_signing_secret: str | None = None
    webhook_enabled: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Booking:
    id: str
    account_id: str
    city: str
    fare: Decimal
    status: str
    created_at: datetime = field(default_factory=datetime.utcnow)
