"""The catalog: which item and relation types exist, their keys, attributes and who owns each attribute.

Written as Pydantic classes (items.py, relations.py). Exported to catalog.json (committed, so every PR shows how it
changed - see compat.py) and synced into the item_type / relation_type tables for clients to read.
"""

from thoth.catalog.base import ANY_TYPE, ItemSpec, RelationSpec, Source, owned_by
from thoth.catalog.registry import ITEMS, RELATIONS, export

__all__ = ["ANY_TYPE", "ITEMS", "RELATIONS", "ItemSpec", "RelationSpec", "Source", "export", "owned_by"]
