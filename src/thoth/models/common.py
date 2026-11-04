"""Pieces shared by items, relations and their history tables."""

from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

TIMESTAMP = sa.DateTime(timezone=True)


def table_of(model: type[SQLModel]) -> sa.Table:
    """The Core table behind a `table=True` model."""
    return SQLModel.metadata.tables[str(model.__tablename__)]


class Status(StrEnum):
    """Where a thing is in its life.

    planned -> active <-> missing; (planned | active | missing) -> decommissioning -> retained -> retired;
    missing -> retired (D53, D85); (decommissioning | retained) -> planned, a restore (D106).
    """

    PLANNED = "planned"  # declared by a request, not seen yet
    ACTIVE = "active"
    MISSING = "missing"  # Anubis found it gone, but a request still declares it
    DECOMMISSIONING = "decommissioning"  # Shikigami's retire pass is taking it down. Only that pass sets it.
    RETAINED = "retained"  # taken down and its trust revoked; its data kept until Charon purges. Only the pass sets it.
    RETIRED = "retired"  # gone - kept (no deletes), with its history. Only Anubis sets it.


def status_type() -> sa.Enum:
    """The column type of a status."""
    # text + CHECK, not a native Postgres enum: adding a status later is one constraint change.
    return sa.Enum(
        Status,
        name="status",
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda e: [m.value for m in e],
    )


class Tracked(SQLModel):
    """What an item or relation has in common: state, provenance, and the stamp of its current version.

    `changed_at/by` and `change_reason` are set by the database trigger, never by the app: they say who made the
    current version and why. History rows keep the same columns for every earlier version.
    """

    status: Status = Field(default=Status.ACTIVE, sa_type=status_type())
    attributes: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    source: str = Field(max_length=64)  # who asserted it exists: shikigami, k8s-scan, authentik-sync, manual, ...
    first_seen: datetime = Field(sa_type=TIMESTAMP)
    last_seen: datetime = Field(sa_type=TIMESTAMP)  # bumped by every scan - not a change, never versioned
    changed_at: datetime | None = Field(default=None, sa_type=TIMESTAMP)
    changed_by: str | None = Field(default=None, max_length=128)
    change_reason: str | None = Field(default=None)
