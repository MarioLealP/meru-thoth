from alembic import command
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import alembic_config


def check(connection: Connection) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = connection
    command.check(cfg)


async def test_models_match_migrations(db: AsyncEngine) -> None:
    """A model or trigger change without its migration fails here, not in production."""
    async with db.connect() as connection:
        await connection.run_sync(check)
