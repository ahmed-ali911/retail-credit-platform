"""User provisioning helpers."""
from __future__ import annotations

import secrets

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


def ensure_system_user(db: Session, username: str) -> User | None:
    """Mock Payment Gateway feature — create the non-interactive
    ``UserRole.system`` account used as ``actor_id`` on webhook-triggered
    state changes. Its password hash is a random value nobody is ever told;
    ``UserRole.system`` matches no route's role allow-list, so this account
    cannot sign in even if the (unknown) password were somehow guessed.

    Returns the created user, or None if it already existed. Called at
    startup for operational visibility (logs once) — but nothing at runtime
    depends on that having already run; see ``ensure_system_actor_id``.
    """
    existing = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        return None
    system_user = User(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=UserRole.system,
        active=True,
    )
    db.add(system_user)
    db.commit()
    db.refresh(system_user)
    return system_user


def ensure_system_actor_id(db: Session, username: str) -> int:
    """Get-or-create the system user's id. Idempotent, safe to call from
    request-handling code (webhook processing) regardless of whether startup
    seeding ran — e.g. the test suite disables startup seeding entirely."""
    existing = db.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    created = ensure_system_user(db, username)
    assert created is not None  # we just proved it didn't exist
    return created.id
