"""Read/write access to externalised business-rule parameters.

Design choice: parameters live in a **DB table** (`config_parameters`), seeded
from `config/business_rules.yaml`.

Why DB-backed and not a plain YAML file read at request time:

  * Operations can change a threshold at runtime (or via a future admin screen)
    without a redeploy or a code review.
  * `updated_at` per row gives a basic change trail; the value that produced a
    decision is also snapshotted onto each AssessmentResult for audit.
  * The assessment service depends on an interface (`get_*`), not on a file
    format, so the source can evolve.

Cost of this choice: one extra table + seeding step, and reads hit the DB.
We accept that; the YAML file still exists as version-controlled defaults and
documentation.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.config_parameter import ConfigParameter

# Canonical keys used by the assessment engine. Nothing else should hardcode
# these strings.
KEY_MIN_INCOME = "minimum_monthly_income"
KEY_MAX_DBR = "maximum_debt_burden_ratio"
KEY_INSTALLMENT_FACTOR = "installment_estimation_factor"
KEY_RISK_AUTO_APPROVE_MIN = "risk_score_auto_approve_min"
KEY_RISK_REFER_MIN = "risk_score_refer_min"


def _cast(raw: str, value_type: str):
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return raw


class ConfigService:
    def __init__(self, db: Session):
        self.db = db

    # --- reads -------------------------------------------------------------
    def get_raw(self, key: str) -> ConfigParameter:
        param = self.db.get(ConfigParameter, key)
        if param is None:
            raise KeyError(f"Business-rule parameter '{key}' is not configured")
        return param

    def get(self, key: str):
        param = self.get_raw(key)
        return _cast(param.value, param.value_type)

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def all(self) -> dict:
        rows = self.db.execute(select(ConfigParameter)).scalars().all()
        return {r.key: _cast(r.value, r.value_type) for r in rows}

    # --- writes ----------------------------------------------------------
    def set(self, key: str, value, value_type: str | None = None,
            description: str | None = None) -> ConfigParameter:
        param = self.db.get(ConfigParameter, key)
        if param is None:
            param = ConfigParameter(
                key=key,
                value=str(value),
                value_type=value_type or _infer_type(value),
                description=description,
            )
            self.db.add(param)
        else:
            param.value = str(value)
            if value_type:
                param.value_type = value_type
            if description is not None:
                param.description = description
        self.db.flush()
        return param

    # --- seeding -------------------------------------------------------
    def seed_from_yaml(self, path: str | Path) -> int:
        """Insert any missing keys from the YAML seed file. Returns count added."""
        path = Path(path)
        if not path.exists():
            return 0
        data = yaml.safe_load(path.read_text()) or {}
        params = data.get("parameters", {})
        added = 0
        for key, spec in params.items():
            if self.db.get(ConfigParameter, key) is not None:
                continue
            self.db.add(
                ConfigParameter(
                    key=key,
                    value=str(spec["value"]),
                    value_type=spec.get("type", "str"),
                    description=(spec.get("description") or "").strip() or None,
                )
            )
            added += 1
        if added:
            self.db.commit()
        return added


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"
