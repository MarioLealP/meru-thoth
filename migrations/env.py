"""Alembic runs from here: our tables (SQLModel metadata) and database-side logic (alembic_utils entities)."""

import asyncio
from logging.config import fileConfig

from alembic import context
from alembic_utils.replaceable_entity import register_entities
from sqlalchemy import Connection

from thoth.db import ENTITIES
from thoth.db.session import make_engine
from thoth.models import Item

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

register_entities(ENTITIES)
target_metadata = Item.metadata  # importing thoth.models registers every table on this one SQLModel.metadata


def run_on(connection: Connection) -> None:
    """Run the migrations over an open connection."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_on_new_connection() -> None:
    """Migrate over a connection of our own (the async engine, as the app uses)."""
    engine = make_engine()
    async with engine.connect() as connection:
        await connection.run_sync(run_on)
    await engine.dispose()


def run_migrations() -> None:
    """Migrate over the sync connection a test or tool handed in, else over a new one."""
    if (connection := config.attributes.get("connection")) is not None:
        run_on(connection)
        return
    asyncio.run(run_on_new_connection())


if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported: the triggers need a live database to diff")
run_migrations()
