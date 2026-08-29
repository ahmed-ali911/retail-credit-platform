"""Create (or confirm) the bootstrap admin user from env vars.

    ADMIN_USERNAME / ADMIN_PASSWORD   (defaults: admin / admin)

    python -m scripts.create_admin
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.users import ensure_admin_user


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        created = ensure_admin_user(db, settings.admin_username, settings.admin_password)
        if created is None:
            print(f"admin user '{settings.admin_username}' already exists")
        else:
            print(f"created admin user '{created.username}'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
