"""Tests run against a real Postgres (scripts/test-db.sh): the behaviour under test lives in the database."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlmodel import Session

from thoth.catalog import check
from thoth.db import make_engine


def alembic_config() -> Config:
    return Config("alembic.ini", toml_file="pyproject.toml")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get("THOTH_TEST_DATABASE_URL")
    if not url:
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
