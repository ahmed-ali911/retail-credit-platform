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

import json
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.config_parameter import ConfigParameter

# Canonical keys. Nothing outside this module should hardcode these strings.
# --- assessment engine (Step 1) ---
KEY_MIN_INCOME = "minimum_monthly_income"
KEY_MAX_DBR = "maximum_debt_burden_ratio"
KEY_INSTALLMENT_FACTOR = "installment_estimation_factor"
KEY_RISK_AUTO_APPROVE_MIN = "risk_score_auto_approve_min"
KEY_RISK_REFER_MIN = "risk_score_refer_min"
# --- pricing / offers (Step 2) ---
KEY_TENOR_PROFIT_RATE_TABLE = "tenor_profit_rate_table"
KEY_MIN_DOWN_PAYMENT_PCT = "minimum_down_payment_pct"
KEY_OFFER_VALIDITY_DAYS = "offer_validity_days"
# --- payments / overdue / late fees (Step 3) ---
KEY_LATE_FEE_RATE = "late_fee_rate"
KEY_LATE_FEE_GRACE_DAYS = "late_fee_grace_period_days"
KEY_LATE_FEE_ONCE_PER_INSTALLMENT = "late_fee_once_per_installment"
KEY_LATE_FEE_MAX_PER_CONTRACT = "late_fee_max_per_contract"  # placeholder, NOT enforced
# --- closure: settlement / cancellation / return (Step 4) — ALL placeholders ---
KEY_EARLY_SETTLEMENT_REBATE_PCT = "early_settlement_profit_rebate_pct"
KEY_DP_REFUND_PCT_CANCELLATION = "down_payment_refund_pct_cancellation"
KEY_DP_REFUND_PCT_RETURN = "down_payment_refund_pct_return"
KEY_OWNERSHIP_TRANSFERS_ON_DELIVERY = "ownership_transfers_on_delivery"
KEY_SETTLEMENT_QUOTE_VALIDITY_DAYS = "settlement_quote_validity_days"


def _cast(raw: str, value_type: str):
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if value_type == "json":
        return json.loads(raw)
    return raw


def _serialize(value, value_type: str | None) -> str:
    if value_type == "json" or isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


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

    def get_json(self, key: str):
        param = self.get_raw(key)
        return json.loads(param.value)

    def all(self) -> dict:
        rows = self.db.execute(select(ConfigParameter)).scalars().all()
        return {r.key: _cast(r.value, r.value_type) for r in rows}

    # --- writes ----------------------------------------------------------
    def set(self, key: str, value, value_type: str | None = None,
            description: str | None = None) -> ConfigParameter:
        param = self.db.get(ConfigParameter, key)
        resolved_type = value_type or (param.value_type if param else None) or _infer_type(value)
        if param is None:
            param = ConfigParameter(
                key=key,
                value=_serialize(value, resolved_type),
                value_type=resolved_type,
                description=description,
            )
            self.db.add(param)
        else:
            param.value = _serialize(value, resolved_type)
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
            value_type = spec.get("type", "str")
            self.db.add(
                ConfigParameter(
                    key=key,
                    value=_serialize(spec["value"], value_type),
                    value_type=value_type,
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
    if isinstance(value, (dict, list)):
        return "json"
    return "str"
