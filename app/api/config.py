from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.config_parameter import ConfigParameter
from app.schemas.config import ConfigParameterOut, ConfigParameterUpdate
from app.services.config_service import ConfigService

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/parameters", response_model=list[ConfigParameterOut])
def list_parameters(db: Session = Depends(get_db)):
    return db.execute(select(ConfigParameter).order_by(ConfigParameter.key)).scalars().all()


@router.put("/parameters/{key}", response_model=ConfigParameterOut)
def update_parameter(
    key: str, payload: ConfigParameterUpdate, db: Session = Depends(get_db)
):
    service = ConfigService(db)
    try:
        service.get_raw(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown parameter '{key}'")
    param = service.set(
        key, payload.value, value_type=payload.value_type, description=payload.description
    )
    db.commit()
    db.refresh(param)
    return param
