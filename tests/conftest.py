"""Tests run against a real Postgres (scripts/test-db.sh): the behaviour under test lives in the database.

The schema is migrated once into a template database; each test gets its own copy of it (CREATE DATABASE ... TEMPLATE),
so no test sees another's rows, nothing depends on order, and tests could run side by side.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import CursorResult, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.tokens import ALL_TEST_PERMISSIONS, KEYS, SETTINGS, mint
from thoth.api import app
from thoth.api.deps import get_engine, get_verifier
from thoth.catalog import check
from thoth.core.auth import Verifier
from thoth.db.session import make_engine


async def run_sql(session: AsyncSession, statement: str) -> CursorResult[Any]:
    """Raw SQL on the session's transaction, flushing first as session.exec does: these tests go around the ORM."""
    await session.flush()
    return await (await session.connection()).execute(text(statement))


def alembic_config() -> Config:
    return Config("alembic.ini", toml_file="pyproject.toml")


async def _sync_catalog(url: str) -> None:
    engine = make_engine(url)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await check.sync(session)
        await session.commit()
    await engine.dispose()


def _admin_url() -> str:
    url = os.environ.get("THOTH_TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI"):  # CI always has its database; skipping there would pass without testing
            pytest.fail("THOTH_TEST_DATABASE_URL not set in CI")
        pytest.skip("THOTH_TEST_DATABASE_URL not set - start one with scripts/test-db.sh")
    return url


def _database(admin: str, name: str) -> str:
    return make_url(admin).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def template() -> Iterator[str]:
    """A migrated database with the catalog synced: every test's database is a copy of it."""
    admin = _admin_url()
    name = f"thoth_template_{uuid.uuid4().hex[:8]}"
    server = create_engine(admin, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'create database "{name}"'))
    try:
        url = _database(admin, name)
        engine = create_engine(url)
        with engine.begin() as connection:
            cfg = alembic_config()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        engine.dispose()
        asyncio.run(_sync_catalog(url))
        yield name
    finally:
        with server.connect() as connection:
            connection.execute(text(f'drop database if exists "{name}" with (force)'))
        server.dispose()


@pytest.fixture
def database(template: str) -> Iterator[str]:
    """The URL of this test's own database: a copy of the template, dropped afterwards."""
    admin = _admin_url()
    name = f"thoth_test_{uuid.uuid4().hex[:12]}"
    server = create_engine(admin, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'create database "{name}" template "{template}"'))
    yield _database(admin, name)
    with server.connect() as connection:
        connection.execute(text(f'drop database if exists "{name}" with (force)'))
    server.dispose()


@pytest_asyncio.fixture
async def db(database: str) -> AsyncIterator[AsyncEngine]:
    """An engine on this test's own migrated database."""
    engine = make_engine(database)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(db, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture
async def anon(db: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """The API on this test's database, with no token - send your own."""
    app.dependency_overrides[get_engine] = lambda: db
    app.dependency_overrides[get_verifier] = lambda: Verifier(settings=SETTINGS, keys=KEYS)
    app.state.catalog_checked = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client(anon: AsyncClient) -> AsyncClient:
    """The API with a test bot's token that may read, correct and report as every source the tests use."""
    anon.headers["Authorization"] = f"Bearer {mint(*ALL_TEST_PERMISSIONS)}"
    return anon
