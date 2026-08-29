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


@lru_cache
def get_settings() -> Settings:
    return Settings()
