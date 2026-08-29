"""Password hashing (bcrypt) and JWT access tokens.

Deliberately minimal for Step 5: short-lived HS256 access tokens, no refresh
tokens, no revocation. `sub` = user id, plus `role` and `exp`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings

# bcrypt rejects secrets longer than 72 bytes; keep hashing predictable.
_MAX_BYTES = 72


def _prep(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(*, sub: str, role: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    minutes = expires_minutes or settings.access_token_expire_minutes
    payload = {
        "sub": str(sub),
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Return the token payload, or raise jwt.PyJWTError on any problem."""
    settings = get_settings()
    return jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
