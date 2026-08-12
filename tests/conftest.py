import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest

import database.db as db
import rate_limit


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """
    rate_limit._attempts is process-wide module state (see rate_limit.py),
    not per-test-app state - without this, login tests in different files
    would share counters across the whole pytest run just because they
    all hit the same rate-limited routes under the TestClient's fixed
    default host, and could trip the limit purely from test count/order
    rather than anything the test itself does.
    """

    rate_limit.clear_all()
    yield
    rate_limit.clear_all()


@pytest.fixture
def isolated_db(monkeypatch):
    """
    Gives each test its own throwaway Postgres schema, created fresh and
    dropped afterward, so tests don't see each other's rows.

    Requires DATABASE_URL to point at a real Postgres instance before
    running pytest (see database/db.py) - e.g.:

        DATABASE_URL=postgresql://user:pass@host/db pytest

    Isolation works via Postgres's per-session search_path: database/db.py's
    get_crm_connection() checks a module-level `_test_schema` hook (unset
    in normal operation) and, when set, runs
    `SET search_path TO "<schema>", public` on every connection it hands
    out - so every unqualified table name transparently resolves inside
    the test's own schema without customer_mapping.py needing to know
    tests exist.
    """

    schema = f"test_{uuid.uuid4().hex[:16]}"

    conn = db.get_crm_connection()
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "_test_schema", schema)

    from crm.customer_mapping import (
        init_customer_mapping,
        init_business_settings,
    )

    init_customer_mapping()
    init_business_settings()

    yield

    # Drop the throwaway schema so test schemas don't accumulate in the
    # shared Postgres instance across a whole pytest run. _test_schema is
    # cleared first so this cleanup connection itself isn't pointed at
    # the schema it's about to drop.
    monkeypatch.setattr(db, "_test_schema", None)
    conn = db.get_crm_connection()
    conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    conn.commit()
    conn.close()


class FakeRequest:
    """
    Minimal stand-in for a Starlette Request - session-only, for any
    future test that needs to call a route handler function directly
    without spinning up a real request/session cookie.
    """

    def __init__(self, session=None):
        self.session = session or {}
