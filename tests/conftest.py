"""Test fixtures.

The suite runs against SQLite by default so it needs no database server. The
application models use only portable column types, so this exercises the same
ORM/service code paths. Set TEST_DATABASE_URL to a Postgres URL to run against
the real engine.

Auth (Step 5): the default `client` fixture is authenticated as an `admin`
(admin is allowed on every endpoint, so Step 1–4 flow tests keep working
unchanged). Use `client_as(role)` for role-specific tests.
"""
from __future__ import annotations

import os

# Must be set before any `app.*` import so the engine is built against it.
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["DISABLE_STARTUP_SEED"] = "true"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.core.database as database  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services.config_service import ConfigService  # noqa: E402

CONFIG_YAML = os.path.join(os.path.dirname(__file__), "..", "config", "business_rules.yaml")

# One bcrypt hash for the whole session — every test user shares this password.
TEST_PASSWORD = "secret123"
_TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)

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
def auth(db):
    """One active user per role. Returns {'users': {...}, 'tokens': {...}}."""
    users: dict[str, User] = {}
    for role in UserRole:
        u = User(
            username=f"u_{role.value}",
            password_hash=_TEST_PASSWORD_HASH,
            role=role,
            active=True,
        )
        db.add(u)
        users[role.value] = u
    db.commit()
    tokens = {
        role: create_access_token(sub=str(u.id), role=u.role.value)
        for role, u in users.items()
    }
    return {"users": users, "tokens": tokens}


@pytest.fixture
def _db_override(db):
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(_db_override, auth):
    """Authenticated as admin by default."""
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {auth['tokens']['admin']}"
        yield c


@pytest.fixture
def client_as(_db_override, auth):
    """Factory: client_as('customer') -> TestClient with that role's token.

    Pass role=None for an unauthenticated client.
    """
    def _make(role: str | None):
        c = TestClient(app)
        if role is not None:
            c.headers["Authorization"] = f"Bearer {auth['tokens'][role]}"
        return c

    return _make


@pytest.fixture
def set_config(db):
    """Helper to change a business-rule parameter mid-test."""
    def _set(key: str, value):
        ConfigService(db).set(key, value)
        db.commit()

    return _set
