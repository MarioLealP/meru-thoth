"""Alembic runs from here: our tables (SQLModel metadata) and database-side logic (alembic_utils entities)."""

from logging.config import fileConfig

from alembic import context
from alembic_utils.replaceable_entity import register_entities
from sqlalchemy import Connection
from sqlmodel import SQLModel

import thoth.models  # noqa: F401  # registers the tables on SQLModel.metadata
from thoth.db import make_engine
from thoth.sql import ENTITIES

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

register_entities(ENTITIES)
target_metadata = SQLModel.metadata


def run_on(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    # A test or tool can hand over its own connection; otherwise connect with THOTH_DATABASE_URL.
    if (connection := config.attributes.get("connection")) is not None:
        run_on(connection)
        return
    with make_engine().connect() as connection:
        run_on(connection)


if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported: the triggers need a live database to diff")
run_migrations()
