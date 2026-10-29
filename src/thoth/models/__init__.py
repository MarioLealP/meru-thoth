"""All tables, imported so SQLModel.metadata (and Alembic) sees them."""

from thoth.models.catalog import ItemType, RelationType
from thoth.models.common import Status
from thoth.models.history import ItemHistory, RelationHistory
from thoth.models.inventory import Item, ItemObservation, Relation, SnapshotRun

__all__ = [
    "Item",
    "ItemHistory",
    "ItemObservation",
    "ItemType",
    "Relation",
    "RelationHistory",
    "RelationType",
    "SnapshotRun",
    "Status",
]
