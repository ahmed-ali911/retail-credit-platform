from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_roles
from app.core.database import get_db
from app.models.config_parameter import ConfigParameter
from app.models.user import User, UserRole
from app.schemas.config import ConfigParameterOut, ConfigParameterUpdate
from app.services.audit import record_event
from app.services.config_service import ConfigService

router = APIRouter(
    prefix="/config",
    tags=["config"],
    dependencies=[Depends(require_roles(UserRole.admin))],
)


@router.get("/parameters", response_model=list[ConfigParameterOut])
def list_parameters(db: Session = Depends(get_db)):
    return db.execute(select(ConfigParameter).order_by(ConfigParameter.key)).scalars().all()


@router.put("/parameters/{key}", response_model=ConfigParameterOut)
def update_parameter(
    key: str,
    payload: ConfigParameterUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(UserRole.admin)),
):
    service = ConfigService(db)
    try:
        existing = service.get_raw(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown parameter '{key}'")
    before_value = existing.value

    param = service.set(
        key, payload.value, value_type=payload.value_type, description=payload.description
    )
    record_event(
        db,
        user_id=actor.id,
        action="config.updated",
        entity_type="config_parameter",
        entity_id=key,
        before={"value": before_value},
        after={"value": param.value},
    )
    db.commit()
    db.refresh(param)
    return param
