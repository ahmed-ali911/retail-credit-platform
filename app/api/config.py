from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.approval import ACTION_CONFIG_UPDATE
from app.models.config_parameter import ConfigParameter
from app.models.user import User, UserRole
from app.schemas.approval import ApprovalRequestOut
from app.schemas.config import ConfigParameterOut, ConfigParameterUpdate
from app.services import approvals as approval_service
from app.services.config_service import ConfigService

router = APIRouter(
    prefix="/config",
    tags=["config"],
    dependencies=[Depends(require_roles(UserRole.admin))],
)


@router.get("/parameters", response_model=list[ConfigParameterOut])
def list_parameters(db: Session = Depends(get_db)):
    return db.execute(select(ConfigParameter).order_by(ConfigParameter.key)).scalars().all()


@router.put(
    "/parameters/{key}",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_parameter_update(
    key: str,
    payload: ConfigParameterUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    """Step 6 behaviour change: this no longer applies the value immediately.

    It creates a **pending** maker-checker `ApprovalRequest`
    (`action_type=config.update`). A *different* `credit_manager`/`admin` must
    `POST /approvals/{id}/approve` for the value — and the `config.updated`
    audit event — to actually happen.
    """
    service = ConfigService(db)
    try:
        service.get_raw(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown parameter '{key}'")

    if approval_service.pending_request_for(db, ACTION_CONFIG_UPDATE, key):
        raise HTTPException(
            status_code=409,
            detail=f"A config-update request for '{key}' is already pending",
        )

    req = approval_service.create_request(
        db,
        action_type=ACTION_CONFIG_UPDATE,
        entity_type="config_parameter",
        entity_id=key,
        requested_by=actor.id,
        payload={
            "new_value": payload.value,
            "value_type": payload.value_type,
            "description": payload.description,
        },
    )
    db.commit()
    db.refresh(req)
    return req
