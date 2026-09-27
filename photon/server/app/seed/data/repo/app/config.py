from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql://meridian:meridian@localhost:5432/meridian"
    redis_url: str = "redis://localhost:6379/1"

    # Webhook delivery
    webhook_delivery_timeout_seconds: int = 5
    webhook_max_attempts: int = 4

    # Auth
    jwt_secret: str = os.environ.get("JWT_SECRET", "dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    session_ttl_minutes: int = 60 * 12

    class Config:
        env_prefix = "MERIDIAN_"


@lru_cache
def get_settings() -> Settings:
    return Settings()
