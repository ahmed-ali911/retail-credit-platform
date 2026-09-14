"""Mock Payment Gateway — settings.

A genuinely separate service from retail-credit-api: its own process, its own
database (a file-based SQLite store, not the Postgres container the main app
uses — see database.py), reached only over HTTP. Nothing here ever imports
from, or writes into, the retail-credit-api codebase or its database.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # This service's own store — deliberately not shared with retail-credit-api.
    # Overridden to an absolute path onto a mounted volume in docker-compose.yml
    # so the data survives container restarts; a relative path is fine for
    # local `uvicorn app.main:app` runs and for tests.
    database_url: str = "sqlite:///./gateway.db"

    # Shared HMAC secret used to sign outbound webhooks — MUST match
    # retail-credit-api's GATEWAY_WEBHOOK_SECRET. Never committed for real use.
    webhook_secret: str = "dev-insecure-shared-webhook-secret-change-me"

    # Where this service's own checkout pages are reachable from a browser
    # (used to build the checkout_url returned from POST /gateway/checkout-sessions).
    public_base_url: str = "http://localhost:8100"

    # Fallback webhook destination if a checkout-session request doesn't supply
    # one explicitly — the retail-credit-api compose service is named "api"
    # (see docker-compose.yml).
    default_webhook_url: str = "http://api:8000/integrations/mock-gateway/webhooks"

    checkout_session_expiry_minutes: int = 15
    # "Delayed settlement" demo scenario — how long after CAPTURED the SETTLED
    # webhook fires, simulating a next-day-batch settlement in miniature.
    # float (not int) so tests can use a sub-second delay.
    delayed_settlement_seconds: float = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
