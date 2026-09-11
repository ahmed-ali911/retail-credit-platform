"""Migration round-trip tests.

These run alembic **out-of-process** (`python -m alembic ...` as a subprocess)
against a throwaway file-based SQLite DB in ``tmp_path`` — never the shared
in-memory engine the rest of the suite uses (``app.core.config.get_settings``
is ``lru_cache``d, so mutating ``DATABASE_URL`` in-process wouldn't reliably
repoint it; a subprocess sidesteps that entirely and is also what an operator
actually runs).

No fixtures from ``conftest.py`` are used here on purpose — this file's whole
point is to exercise migrations against a database the rest of the suite never
touches.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic(*args: str, db_path: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


_APP_FLOW_SCRIPT = textwrap.dedent(
    """
    # Activates one contract, then runs ECL once, against $DATABASE_URL —
    # produces the day-one origination assessment (run_id=NULL) AND a
    # same-day portfolio-run assessment for the same (contract_id, as_of_date).
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.core.database as database

    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    database.engine = engine
    database.SessionLocal = SessionLocal

    from app.core.database import get_db
    from app.core.security import create_access_token, hash_password
    from app.main import app
    from app.models.user import User, UserRole
    from app.services.config_service import ConfigService
    from fastapi.testclient import TestClient
    from tests.helpers import active_contract

    db = SessionLocal()
    ConfigService(db).seed_from_yaml("config/business_rules.yaml")
    db.commit()

    def _override():
        yield db
    app.dependency_overrides[get_db] = _override

    u = User(username="admin", password_hash=hash_password("secret123"),
             role=UserRole.admin, active=True)
    db.add(u)
    db.commit()
    token = create_access_token(sub=str(u.id), role=u.role.value)

    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        active_contract(c, national_id="MIGRATION-CHECK")
        r = c.post("/ecl/run", json={})
        assert r.status_code == 200, r.text
    db.close()
    """
)


def _run_app_flow(db_path: Path, script_path: Path) -> subprocess.CompletedProcess:
    script_path.write_text(_APP_FLOW_SCRIPT)
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{db_path}"
    env["DISABLE_STARTUP_SEED"] = "true"
    env.setdefault("JWT_SECRET_KEY", "migration-test-secret-key")
    # the script does `import app...` / `from tests.helpers import ...` — needs
    # the repo root on sys.path, which running `python script.py` from tmp_path
    # would not give it for free.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_downgrade_survives_same_day_origination_and_run_assessments(tmp_path):
    """Reproduces the pre-commit review finding: a contract's day-one
    origination assessment (``run_id=NULL``) and a same-day ``POST /ecl/run``
    assessment share ``(contract_id, as_of_date)`` — the ordinary first-day
    flow, not a contrived edge case. ``downgrade()`` must survive it.

    "Sane" leftover state (asserted below): exactly one row per
    ``(contract_id, as_of_date)`` survives the collapse, and it is the
    *newest* one (highest id — the run assessment, which reflects the fuller,
    more recent calculation) rather than the origination row.
    """
    db_path = tmp_path / "migration_test.db"

    up = _alembic("upgrade", "head", db_path=db_path)
    assert up.returncode == 0, up.stdout + up.stderr

    flow = _run_app_flow(db_path, tmp_path / "flow.py")
    assert flow.returncode == 0, flow.stdout + flow.stderr

    # sanity: the collision this test is about actually exists before we
    # attempt the downgrade — otherwise the test would pass for the wrong
    # reason.
    con = sqlite3.connect(db_path)
    try:
        before = con.execute(
            "SELECT id, run_id, contract_id, as_of_date FROM ecl_assessments ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    assert len(before) == 2, before
    origination, run_assessment = before
    assert origination[1] is None, "expected row 1 to be the origination assessment"
    assert run_assessment[1] is not None, "expected row 2 to be the run assessment"
    assert origination[2] == run_assessment[2], "expected the same contract_id"
    assert origination[3] == run_assessment[3], "expected the same as_of_date — the collision"

    down = _alembic("downgrade", "-1", db_path=db_path)
    assert down.returncode == 0, down.stdout + down.stderr

    con = sqlite3.connect(db_path)
    try:
        after = con.execute(
            "SELECT id, contract_id, as_of_date FROM ecl_assessments"
        ).fetchall()
    finally:
        con.close()
    assert len(after) == 1, after
    assert after[0][0] == run_assessment[0], (
        "the newest (run) assessment should have survived the collapse, "
        "not the origination row"
    )


def test_full_migration_chain_up_and_down(tmp_path):
    """The rest of the chain (0001..head) round-trips cleanly on a DB that was
    never actually used — the ordinary "empty schema" case, unaffected by the
    duplicate-row fix above."""
    db_path = tmp_path / "chain_test.db"

    up = _alembic("upgrade", "head", db_path=db_path)
    assert up.returncode == 0, up.stdout + up.stderr

    down = _alembic("downgrade", "base", db_path=db_path)
    assert down.returncode == 0, down.stdout + down.stderr

    up_again = _alembic("upgrade", "head", db_path=db_path)
    assert up_again.returncode == 0, up_again.stdout + up_again.stderr
