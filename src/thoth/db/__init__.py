"""Database-side logic (functions, triggers, grants), declared as alembic_utils entities so migrations track them."""

from thoth.db.entities import ENTITIES

__all__ = ["ENTITIES"]
