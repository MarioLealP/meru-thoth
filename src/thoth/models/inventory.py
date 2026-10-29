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
    """What one source reported about one item from one scope (where it looked) - its slice. The API merges the
    slices into `item.attributes`, so observations churn with every scan and are not versioned; the merged item is.
    """

    __tablename__ = "item_observation"

    item_id: UUID = Field(foreign_key="item.id", primary_key=True)
    source: str = Field(primary_key=True, max_length=64)
    scope: str = Field(primary_key=True, max_length=128)
    attributes: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    first_seen: datetime = Field(sa_type=TIMESTAMP)
    last_seen: datetime = Field(sa_type=TIMESTAMP)
    # Snapshots in a row from this source + scope without the item. At 3 it is on Anubis's list (D53).
    missed_scans: int = Field(default=0, ge=0)


class SnapshotRun(SQLModel, table=True):
    """One snapshot, accepted or refused - a log, so Zabbix can see a source that stopped or broke (D51)."""

    __tablename__ = "snapshot_run"
    __table_args__ = (sa.Index("ix_snapshot_run_source_scope_at", "source", "scope", "at"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source: str = Field(max_length=64)
    scope: str = Field(max_length=128)
    at: datetime = Field(sa_type=TIMESTAMP)
    accepted: bool
    summary: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)  # counts, or the reasons it was refused
