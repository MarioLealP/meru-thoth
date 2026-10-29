"""The inventory: items (configuration items), the relations between them, and each source's slice."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from thoth.models.common import TIMESTAMP, Tracked


class Item(Tracked, table=True):
    """One thing: a host, an app, an account, a volume... `id` never changes; `type` + `key` is how the outside
    world (and every scanner) finds it."""

    __tablename__ = "item"
    __table_args__ = (sa.UniqueConstraint("type", "key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    type: str = Field(foreign_key="item_type.name", max_length=64)
    key: str
    name: str


class Relation(Tracked, table=True):
    """A typed, directed link, dependent first: `from_item` <type> `to_item` ("recipes uses recipes-db")."""

    __tablename__ = "relation"
    __table_args__ = (
        sa.UniqueConstraint("type", "from_item", "to_item"),
        sa.CheckConstraint("from_item <> to_item", name="relation_not_to_itself"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    type: str = Field(foreign_key="relation_type.name", max_length=64)
    from_item: UUID = Field(foreign_key="item.id", index=True)
    to_item: UUID = Field(foreign_key="item.id", index=True)


class ItemObservation(SQLModel, table=True):
    """What one source reported about one item - its slice. The API merges the slices into `item.attributes`, so
    observations churn with every scan and are not versioned; the merged item is."""

    __tablename__ = "item_observation"

    item_id: UUID = Field(foreign_key="item.id", primary_key=True)
    source: str = Field(primary_key=True, max_length=64)
    attributes: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    first_seen: datetime = Field(sa_type=TIMESTAMP)
    last_seen: datetime = Field(sa_type=TIMESTAMP)
