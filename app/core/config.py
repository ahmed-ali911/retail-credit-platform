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

    # SQLAlchemy URL for the running application.
    database_url: str = "postgresql+psycopg2://retail:retail@localhost:5432/retail_credit"

    # Seed file for business-rule parameters.
    business_rules_file: str = "config/business_rules.yaml"

    # When true (used by the test suite), startup does not attempt to seed.
    disable_startup_seed: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
