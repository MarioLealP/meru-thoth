"""The catalog: which kinds of items and relations exist.

Defined in code and loaded by migrations - git is its history, so these tables have no history tables of their own.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel


class ItemType(SQLModel, table=True):
    """An item type, copied from the catalog at startup."""

    __tablename__ = "item_type"

    name: str = Field(primary_key=True, max_length=64)  # host, app, account, ...
    description: str
    key_pattern: str  # regex every key of this type must match, so all sources spell a thing the same way
    attribute_schema: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)  # JSON Schema for attributes


class RelationType(SQLModel, table=True):
    """A relation type, copied from the catalog at startup."""

    __tablename__ = "relation_type"

    name: str = Field(primary_key=True, max_length=64)  # mounts, owned-by, part-of, ...
    description: str
    from_types: list[str] = Field(sa_type=ARRAY(sa.Text))  # item types allowed at the dependent end
    to_types: list[str] = Field(sa_type=ARRAY(sa.Text))
    attribute_schema: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
