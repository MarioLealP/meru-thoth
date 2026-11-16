"""The running app's role, thoth_app (D58): it writes rows and nothing else - never history, schema or deletes.

Each test switches to the role for one transaction (SET LOCAL ROLE), as the app's own login would be.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.db.session import write_as
from thoth.models import Item, ItemHistory

APP = "thoth_app"
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
READ_WRITE = {"SELECT", "INSERT", "UPDATE"}
READ = {"SELECT"}

# Every table and what the app may do with it. A new table fails test_grants_match until it's listed here and its
# migration grants exactly this - so no table ever gets rights by accident (or none by forgetting).
EXPECTED = {
    "item": READ_WRITE,
    "relation": READ_WRITE,
    "item_observation": READ_WRITE,
    "relation_observation": READ_WRITE,
    "snapshot_run": READ_WRITE,
    "item_type": READ_WRITE,
    "relation_type": READ_WRITE,
    "item_history": READ,
    "relation_history": READ,
    "alembic_version": READ,
}


@pytest.fixture
async def as_app(db: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose transaction runs as thoth_app."""
    async with AsyncSession(db, expire_on_commit=False) as session:
        await (await session.connection()).execute(text(f"SET LOCAL ROLE {APP}"))
        yield session


def host(key: str = "clockmost") -> Item:
    now = datetime.now(UTC)
    return Item(type="host", key=key, name=key, source="test", first_seen=now, last_seen=now)


# A privilege counts when it covers the whole table: granted on the table, or on every one of its columns (how
# alembic_utils grants). Granted on only some columns, it shows up as "partial" - also a failure.
COVERAGE = text("""
    SELECT t.tablename, p.privilege,
           bool_or(has_table_privilege(:role, format('public.%I', t.tablename), p.privilege)) AS whole,
           bool_and(p.privilege = ANY(:column_level)
                   AND has_column_privilege(:role, format('public.%I', t.tablename), c.attname, p.privilege)) AS every,
           bool_or(p.privilege = ANY(:column_level)
                   AND has_column_privilege(:role, format('public.%I', t.tablename), c.attname, p.privilege)) AS some
    FROM pg_tables t
    JOIN pg_attribute c ON c.attrelid = format('public.%I', t.tablename)::regclass AND c.attnum > 0
                       AND NOT c.attisdropped
    CROSS JOIN unnest(cast(:privileges AS text[])) AS p(privilege)
    WHERE t.schemaname = 'public'
    GROUP BY t.tablename, p.privilege
""")
COLUMN_LEVEL = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


async def test_grants_match(session: AsyncSession) -> None:
    granted: dict[str, set[str]] = {}
    for table, privilege, whole, every, some in await (await session.connection()).execute(
        COVERAGE, {"role": APP, "privileges": list(PRIVILEGES), "column_level": list(COLUMN_LEVEL)}
    ):
        rights = granted.setdefault(table, set())
        if whole or (privilege in COLUMN_LEVEL and every):
            rights.add(privilege)
        elif privilege in COLUMN_LEVEL and some:
            rights.add(f"partial {privilege}")
    assert granted == EXPECTED


async def test_app_role_is_plain(session: AsyncSession) -> None:
    connection = await session.connection()
    role = (
        await connection.execute(
            text("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": APP},
        )
    ).one()
    assert tuple(role) == (False, False, False, False)
    owned = (
        await connection.execute(
            text("SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = :r"),
            {"r": APP},
        )
    ).scalar_one()
    assert owned == 0
    may_create = await (await session.connection()).execute(
        text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": APP}
    )
    assert may_create.scalar_one() is False


async def test_app_writes_and_history_still_records(db: AsyncEngine, as_app: AsyncSession) -> None:
    item = host()
    await write_as(as_app, "test", "first scan")
    as_app.add(item)
    await as_app.commit()
    await (await as_app.connection()).execute(text(f"SET LOCAL ROLE {APP}"))
    await write_as(as_app, "test", "renamed")
    item.name = "clockmost-renamed"
    as_app.add(item)
    await as_app.commit()
    async with AsyncSession(db) as session:
        versions = (await session.exec(select(ItemHistory).where(ItemHistory.id == item.id))).all()
    assert [v.name for v in versions] == ["clockmost"]  # the trigger wrote it, as its owner


async def test_app_cannot_write_history(as_app: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="permission denied for table item_history"):
        await (await as_app.connection()).execute(text("INSERT INTO item_history DEFAULT VALUES"))


async def test_app_cannot_delete(as_app: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="permission denied for table item"):
        await (await as_app.connection()).execute(text("DELETE FROM item"))


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE item DISABLE TRIGGER item_track_changes",
        "DROP TABLE relation_history",
        "CREATE OR REPLACE FUNCTION thoth_append_only() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$",
        "CREATE TABLE sneaky (id int)",
    ],
)
async def test_app_cannot_change_schema(as_app: AsyncSession, statement: str) -> None:
    with pytest.raises(DBAPIError, match=r"must be owner|permission denied"):
        await (await as_app.connection()).execute(text(statement))
