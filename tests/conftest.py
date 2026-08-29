"""Test fixtures.

The suite runs against SQLite by default so it needs no database server. The
application models use only portable column types, so this exercises the same
ORM/service code paths. Set TEST_DATABASE_URL to a Postgres URL to run against
the real engine.
"""
from __future__ import annotations

import os

# Must be set before any `app.*` import so the engine is built against it.
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["DISABLE_STARTUP_SEED"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.core.database as database  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.config_service import ConfigService  # noqa: E402

CONFIG_YAML = os.path.join(os.path.dirname(__file__), "..", "config", "business_rules.yaml")

if _TEST_DB_URL.startswith("sqlite") and ":memory:" in _TEST_DB_URL:
    engine = create_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
else:
    engine = create_engine(_TEST_DB_URL, future=True)

TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# Point the app's module-level session factory at the test engine too, so the
# few code paths that use SessionLocal directly hit the same database.
database.engine = engine
database.SessionLocal = TestingSessionLocal


@pytest.fixture
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db(_schema):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def seed_config(db):
    """Seed the fictitious default business-rule parameters for every test."""
    ConfigService(db).seed_from_yaml(CONFIG_YAML)
    yield


@pytest.fixture
def client(db):
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def set_config(db):
    """Helper to change a business-rule parameter mid-test."""
    def _set(key: str, value):
        ConfigService(db).set(key, value)
        db.commit()

    return _set
