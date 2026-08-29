"""Reusable auth dependencies: authentication, role checks, ownership checks."""
from __future__ import annotations

from collections.abc import Iterable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.credit_application import CreditApplication
from app.models.customer import Customer
from app.models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    if not token:
        raise _UNAUTHENTICATED
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


def _role_values(roles: Iterable) -> set[str]:
    return {r.value if isinstance(r, UserRole) else str(r) for r in roles}


def require_roles(*roles):
    """Dependency factory: 403 unless the caller's role is one of `roles`."""
    allowed = _role_values(roles)

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role.value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role.value}' is not permitted for this action "
                    f"(allowed: {', '.join(sorted(allowed))})"
                ),
            )
        return user

    return _dep


# --- ownership: "staff role OR the customer this record belongs to" --------- #

def _customer_for_user(db: Session, user: User) -> Customer | None:
    if user.role != UserRole.customer:
        return None
    return db.execute(
        select(Customer).where(Customer.user_id == user.id)
    ).scalar_one_or_none()


def authorize_owner_or_roles(
    db: Session, user: User, *, staff_roles, owner_customer_id: int | None
) -> None:
    """Allow if the user has a listed staff role, or is the owning customer.

    A customer hitting someone else's record gets 403 (not 404).
    """
    if user.role.value in _role_values(staff_roles):
        return
    if user.role == UserRole.customer and owner_customer_id is not None:
        cust = _customer_for_user(db, user)
        if cust is not None and cust.id == owner_customer_id:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not permitted to access this resource",
    )


def contract_owner_customer_id(db: Session, contract) -> int | None:
    """Customer id behind a contract: contract -> sales_order -> application."""
    sales_order = contract.sales_order
    if sales_order is None:
        return None
    application = db.get(CreditApplication, sales_order.application_id)
    return application.customer_id if application else None
