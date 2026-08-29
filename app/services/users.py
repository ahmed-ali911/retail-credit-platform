"""User provisioning helpers."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User, UserRole


def ensure_admin_user(db: Session, username: str, password: str) -> User | None:
    """Create the bootstrap admin if no user with that username exists.

    Returns the created user, or None if it already existed.
    """
    existing = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        return None
    admin = User(
        username=username,
        password_hash=hash_password(password),
        role=UserRole.admin,
        active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
