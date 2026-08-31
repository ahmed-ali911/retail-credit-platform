from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api import (
    applications,
    approvals,
    audit,
    auth,
    closure,
    collections,
    config as config_api,
    customers,
    offers,
    payments,
    products,
    reconciliation,
)
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.config_service import ConfigService
from app.services.users import ensure_admin_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.disable_startup_seed:
        db = SessionLocal()
        try:
            added = ConfigService(db).seed_from_yaml(settings.business_rules_file)
            if added:
                print(f"[config] seeded {added} business-rule parameter(s)")
            created = ensure_admin_user(
                db, settings.admin_username, settings.admin_password
            )
            if created is not None:
                print(f"[auth] created bootstrap admin '{created.username}'")
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

# Everything else requires authentication; individual routes add role checks.
_authed = [Depends(get_current_user)]
app.include_router(customers.router, dependencies=_authed)
app.include_router(products.router, dependencies=_authed)
app.include_router(applications.router, dependencies=_authed)
app.include_router(offers.router, dependencies=_authed)
app.include_router(payments.router, dependencies=_authed)
app.include_router(closure.router, dependencies=_authed)
app.include_router(collections.router, dependencies=_authed)
app.include_router(approvals.router, dependencies=_authed)
app.include_router(reconciliation.router, dependencies=_authed)
app.include_router(config_api.router, dependencies=_authed)
app.include_router(audit.router, dependencies=_authed)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
