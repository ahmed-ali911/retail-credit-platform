from __future__ import annotations

from pydantic import BaseModel


class ConfigParameterOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None = None


class ConfigParameterUpdate(BaseModel):
    value: bool | int | float | dict | list | str
    value_type: str | None = None
    description: str | None = None
