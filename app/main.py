from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    applications,
    config as config_api,
    customers,
    offers,
    payments,
    products,
)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.config_service import ConfigService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.disable_startup_seed:
        db = SessionLocal()
        try:
            added = ConfigService(db).seed_from_yaml(settings.business_rules_file)
            if added:
                print(f"[config] seeded {added} business-rule parameter(s)")
        finally:
            db.close()
    yield


app = FastAPI(
    title="Retail Credit & Installment Sales Platform",
    description=(
        "Step 1: Customer management, application origination and a rules-based "
        "credit assessment engine. No cash is ever disbursed — the customer buys "
        "a product on installment terms."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(customers.router)
app.include_router(products.router)
app.include_router(applications.router)
app.include_router(offers.router)
app.include_router(payments.router)
app.include_router(config_api.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
