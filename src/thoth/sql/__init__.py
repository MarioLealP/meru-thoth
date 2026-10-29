"""Database-side logic (functions, triggers), declared as alembic_utils entities so migrations track them."""

from thoth.sql.history import ENTITIES

__all__ = ["ENTITIES"]
