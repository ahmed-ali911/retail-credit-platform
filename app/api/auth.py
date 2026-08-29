from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_roles
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.audit import record_event

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if user is None or not user.active or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    settings = get_settings()
    token = create_access_token(sub=str(user.id), role=user.role.value)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/register", response_model=UserOut, status_code=status.HTTP_201_CREATED
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    if db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="username already taken"
        )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        active=True,
    )
    db.add(user)
    db.flush()
    record_event(
        db,
        user_id=actor.id,
        action="user.registered",
        entity_type="user",
        entity_id=user.id,
        after={"username": user.username, "role": user.role.value},
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
