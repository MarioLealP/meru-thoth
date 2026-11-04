"""Earlier versions of items and relations, copied by the database trigger before every real change.

Same columns as the live tables, plus the version's validity window. Append-only: the trigger refuses updates and
deletes here too. "As of <date>" questions read only these tables.
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlmodel import Field

from thoth.models.common import TIMESTAMP, Tracked


class _Version(Tracked):
    history_id: UUID = Field(default_factory=uuid4, primary_key=True)
    valid_from: datetime | None = Field(default=None, sa_type=TIMESTAMP)  # = the old version's changed_at
    valid_to: datetime = Field(sa_type=TIMESTAMP)  # when it was replaced


class ItemHistory(_Version, table=True):
    """An earlier version of an item."""

    __tablename__ = "item_history"
    __table_args__ = (sa.Index("ix_item_history_id", "id"),)

    id: UUID = Field(foreign_key="item.id")
    type: str = Field(max_length=64)
    key: str
    name: str


class RelationHistory(_Version, table=True):
    """An earlier version of a relation."""

    __tablename__ = "relation_history"
    __table_args__ = (sa.Index("ix_relation_history_id", "id"),)

    id: UUID = Field(foreign_key="relation.id")
    type: str = Field(max_length=64)
    from_item: UUID
    to_item: UUID
