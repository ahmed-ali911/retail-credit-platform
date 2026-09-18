from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api import (
    accounting,
    applications,
    approvals,
    audit,
    auth,
    closure,
    collections,
    config as config_api,
    customers,
    ecl,
    gateway_settlement,
    offers,
    payment_gateway,
    payments,
    products,
    reconciliation,
    reports,
    write_off,
)
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services import coa as coa_service
from app.services.config_service import ConfigService
from app.services.users import ensure_admin_user, ensure_system_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.disable_startup_seed:
        db = SessionLocal()
        try:
            added = ConfigService(db).seed_from_yaml(settings.business_rules_file)
            if added:
                print(f"[config] seeded {added} business-rule parameter(s)")
            coa_seed = coa_service.seed_demo_data(db)
            if coa_seed["accounts_created"]:
                print(
                    f"[accounting] seeded {coa_seed['accounts_created']} demo chart-of-accounts "
                    f"row(s) and {coa_seed['mappings_created']} demo posting mapping(s)"
                )
            created = ensure_admin_user(
                db, settings.admin_username, settings.admin_password
            )
            if created is not None:
                print(f"[auth] created bootstrap admin '{created.username}'")
            system_user = ensure_system_user(db, settings.system_gateway_username)
            if system_user is not None:
                print(f"[auth] created system account '{system_user.username}'")
        finally:
            db.close()
    yield


app = FastAPI(
    title="Retail Credit & Installment Sales Platform",
    description=(
        "Installment-sale platform: application → credit assessment → offer → "
        "contract → payments/allocation → settlement/cancellation/return. "
        "No cash is ever disbursed. All endpoints (except /auth/login and "
        "/health) require a bearer token; sensitive actions are role-gated."
    ),
    version="0.5.0",
    lifespan=lifespan,
)

# /auth/login is open; /auth/me and /auth/register guard themselves.
app.include_router(auth.router)

# The Mock Payment Gateway's inbound webhook is the one other unauthenticated
# route: the gateway has no user JWT, it authenticates via the HMAC signature
# verified inside gateway_webhooks.py instead. Registered before _authed is
# even defined, deliberately mirroring auth.router above.
app.include_router(payment_gateway.webhook_router)

# Everything else requires authentication; individual routes add role checks.
_authed = [Depends(get_current_user)]
app.include_router(customers.router, dependencies=_authed)
app.include_router(products.router, dependencies=_authed)
app.include_router(applications.router, dependencies=_authed)
app.include_router(offers.router, dependencies=_authed)
app.include_router(payments.router, dependencies=_authed)
app.include_router(payment_gateway.router, dependencies=_authed)
app.include_router(gateway_settlement.router, dependencies=_authed)
app.include_router(closure.router, dependencies=_authed)
app.include_router(collections.router, dependencies=_authed)
app.include_router(approvals.router, dependencies=_authed)
app.include_router(reconciliation.router, dependencies=_authed)
app.include_router(accounting.router, dependencies=_authed)
app.include_router(ecl.router, dependencies=_authed)
app.include_router(write_off.router, dependencies=_authed)
app.include_router(reports.router, dependencies=_authed)
app.include_router(config_api.router, dependencies=_authed)
app.include_router(audit.router, dependencies=_authed)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
