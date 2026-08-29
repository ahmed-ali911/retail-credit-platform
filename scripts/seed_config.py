"""Seed business-rule parameters from the YAML file into the database.

Idempotent: existing keys are left untouched. Runs automatically on app
startup too; this script is for manual / CI use.

    python -m scripts.seed_config
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.config_service import ConfigService


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        added = ConfigService(db).seed_from_yaml(settings.business_rules_file)
        print(f"Seeded {added} new parameter(s) from {settings.business_rules_file}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
