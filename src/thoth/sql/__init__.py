"""Database-side logic (functions, triggers, grants), declared as alembic_utils entities so migrations track them."""

from thoth.sql import grants, history

ENTITIES = [*history.ENTITIES, *grants.ENTITIES]

__all__ = ["ENTITIES"]
