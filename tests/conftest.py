"""Tests run against a real Postgres (scripts/test-db.sh): the behaviour under test lives in the database."""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import CursorResult, Engine, text
from sqlmodel import Session

from tests.tokens import ALL_TEST_PERMISSIONS, KEYS, SETTINGS, mint
from thoth.api import app
from thoth.api.deps import get_engine, get_verifier
from thoth.catalog import check
from thoth.core.auth import Verifier
from thoth.db.session import make_engine


def run_sql(session: Session, statement: str) -> CursorResult[Any]:
    """Raw SQL on the session's transaction, flushing first as session.exec does: these tests go around the ORM."""
    session.flush()
    return session.connection().execute(text(statement))


def alembic_config() -> Config:
    return Config("alembic.ini", toml_file="pyproject.toml")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get("THOTH_TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI"):  # CI always has its database; skipping there would pass without testing
            pytest.fail("THOTH_TEST_DATABASE_URL not set in CI")
        pytest.skip("THOTH_TEST_DATABASE_URL not set - start one with scripts/test-db.sh")
    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Engine:
    """A freshly migrated, empty database for each test."""
    with engine.begin() as connection:
        connection.execute(text("drop schema public cascade; create schema public"))
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
    with Session(engine) as session:
        check.sync(session)
        session.commit()
    return engine


@pytest.fixture
def session(db: Engine) -> Iterator[Session]:
    with Session(db, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def anon(db: Engine) -> Iterator[TestClient]:
    """The API on a fresh database, with no token - send your own."""
    app.dependency_overrides[get_engine] = lambda: db
    app.dependency_overrides[get_verifier] = lambda: Verifier(settings=SETTINGS, keys=KEYS)
    app.state.catalog_checked = False
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client(anon: TestClient) -> TestClient:
    """The API with a test bot's token that may read, correct and report as every source the tests use."""
    anon.headers["Authorization"] = f"Bearer {mint(*ALL_TEST_PERMISSIONS)}"
    return anon
