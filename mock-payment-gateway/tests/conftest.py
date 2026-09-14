"""Test fixtures for the mock-payment-gateway service.

Mirrors the pattern used by the main retail-credit-api test suite
(tests/conftest.py): an in-memory SQLite engine set up before any `app.*`
import, with the module-level engine/SessionLocal repointed at it and the
FastAPI `get_db` dependency overridden — so every code path (request
handlers AND the background-thread delayed-settlement path, which builds
its own session via SessionLocal) hits the same test database.
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["DEFAULT_WEBHOOK_URL"] = "http://testserver-receiver/webhooks"
os.environ["DELAYED_SETTLEMENT_SECONDS"] = "0.3"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.database as database  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

database.engine = engine
database.SessionLocal = TestingSessionLocal


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _schema():
    from app import models  # noqa: F401 — register tables on Base.metadata

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
