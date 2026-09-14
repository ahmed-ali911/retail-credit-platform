"""Application (infrastructure) settings.

This is deliberately separate from *business-rule* configuration. Anything that
influences a credit decision lives in the `config_parameters` table and is read
through `app.services.config_service` — never from here and never hardcoded in
the assessment logic.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLAlchemy URL for the running application. Host port 5544 matches the
    # docker-compose db mapping (5544 -> container 5432).
    database_url: str = "postgresql+psycopg2://retail:retail@localhost:5544/retail_credit"

    # Seed file for business-rule parameters.
    business_rules_file: str = "config/business_rules.yaml"

    # When true (used by the test suite), startup does not attempt to seed.
    disable_startup_seed: bool = False

    # --- auth (Step 5) ---
    jwt_secret_key: str = "dev-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # Bootstrap admin, created on startup if it does not exist.
    admin_username: str = "admin"
    admin_password: str = "admin"

    # --- Mock Payment Gateway integration ---
    # Base URL this application uses to call the separate mock-payment-gateway
    # service (POST /gateway/checkout-sessions, etc.) — service-to-service,
    # inside the docker-compose network by default.
    gateway_base_url: str = "http://mock-payment-gateway:8100"
    # The URL the *gateway* is told to POST webhooks back to (this app's own
    # endpoint) — inside docker-compose, this app's own service name/port
    # (the compose service is named "api" — see docker-compose.yml).
    gateway_webhook_receive_url: str = (
        "http://api:8000/integrations/mock-gateway/webhooks"
    )
    # Where the browser is sent back after the hosted checkout page finishes
    # (section 5's "Return to merchant" button) — the frontend origin.
    frontend_base_url: str = "http://localhost:5173"
    # Shared HMAC secret used to sign/verify webhooks between the two
    # services. MUST be overridden (and identical on both sides) outside a
    # local demo — never committed for real use. See .env.example.
    gateway_webhook_secret: str = "dev-insecure-shared-webhook-secret-change-me"
    # Webhook timestamps older than this are rejected as stale/replayed
    # (section 10 — replay protection).
    gateway_webhook_max_age_seconds: int = 300
    # Bootstrap service account, created on startup if it does not exist —
    # the `actor_id` attributed to state changes triggered by a verified
    # gateway webhook (no interactive user initiates those). Never used to
    # log in interactively; it has no meaningful password.
    system_gateway_username: str = "system.gateway"


@lru_cache
def get_settings() -> Settings:
    return Settings()
