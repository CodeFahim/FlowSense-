"""Test harness: real PostgreSQL (flowsense_test), fresh schema per run via Alembic.

Each test seeds its own fixture business (the seed archives the previous one),
so tests never share mutable state and nothing is deleted.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

_test_url = os.environ.get("FLOWSENSE_TEST_DATABASE_URL")
if not _test_url:
    raise RuntimeError("Set FLOWSENSE_TEST_DATABASE_URL (scripts/test.ps1 does this).")
os.environ["FLOWSENSE_DATABASE_URL"] = _test_url
os.environ.setdefault("FLOWSENSE_ENV", "test")
os.environ.setdefault("FLOWSENSE_DEMO_CONTROLS_ENABLED", "true")
os.environ.setdefault("FLOWSENSE_FX_PROVIDER", "off")  # tests never call live rate providers
os.environ.setdefault("FLOWSENSE_TRUSTED_PROXY_HOPS", "1")  # tests set X-Forwarded-For per simulated client
os.environ.setdefault("FLOWSENSE_SIGNUPS_PER_HOUR", "1000")  # the suite creates >100 accounts; the production default stays 100

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402

HERE = os.path.dirname(os.path.dirname(__file__))
OWNER = "demo@flowsense.example"
STAFF = "demo-staff@flowsense.example"
OTHER = "owner@harbortea.example"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    assert "flowsense_test" in _test_url, "Refusing to run tests against a non-test database"
    eng = create_engine(_test_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
def db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def signup(email: str, password: str, **extra: Any) -> dict[str, Any]:
    """A complete sign-up body (step 1 email + step 2 details), with a unique username per email."""
    body = {
        "email": email,
        "full_name": "Test User",
        "username": "u" + uuid.uuid5(uuid.NAMESPACE_URL, email.lower()).hex[:12],
        "password": password,
        "confirm_password": password,
    }
    return {**body, **extra}


def seed(scenario: str = "primary") -> uuid.UUID:
    from app.seed.fixtures import seed_fixture

    with get_sessionmaker()() as s:
        return seed_fixture(s, scenario).id


@pytest.fixture
def password() -> str:
    pw = get_settings().demo_password
    assert pw is not None, "FLOWSENSE_DEMO_PASSWORD must be set for tests"
    return pw.get_secret_value()


class Api:
    """Small wrapper: a logged-in TestClient that sends the CSRF header on mutations."""

    def __init__(self, client: TestClient, csrf: str, me: dict[str, Any]) -> None:
        self.c, self.csrf, self.me = client, csrf, me

    def get(self, path: str, **kw: Any) -> Any:
        return self.c.get(path, **kw)

    def post(self, path: str, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        h = {"X-CSRF-Token": self.csrf, **(headers or {})}
        return self.c.post(path, json=json if json is not None else {}, headers=h)


def login(email: str, password: str) -> Api:
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return Api(client, r.json()["csrf_token"], r.json())


@pytest.fixture
def as_owner(password: str) -> Any:
    return lambda: login(OWNER, password)


@pytest.fixture
def as_staff(password: str) -> Any:
    return lambda: login(STAFF, password)


def find_action(api: Api, type_: str, status: str | None = None) -> dict[str, Any]:
    acts = [a for a in api.get("/api/actions").json() if a["type"] == type_ and (status is None or a["status"] == status)]
    assert acts, f"no {type_} action with status {status}"
    return acts[0]


def execute(api: Api, action_id: str, key: str | None = None) -> Any:
    return api.post(f"/api/actions/{action_id}/execute", headers={"Idempotency-Key": key or uuid.uuid4().hex})
