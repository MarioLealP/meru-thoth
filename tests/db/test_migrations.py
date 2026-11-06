from alembic import command
from sqlalchemy import Engine

from tests.conftest import alembic_config


def test_models_match_migrations(db: Engine) -> None:
    """A model or trigger change without its migration fails here, not in production."""
    with db.begin() as connection:
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.check(cfg)
