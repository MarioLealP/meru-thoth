"""The catalog: which item and relation types exist, their keys, attributes and who owns each attribute.

Written as Pydantic classes (items.py, relations.py). Exported to catalog.json (committed, so every PR shows how it
changed - see compat.py) and synced into the item_type / relation_type tables for clients to read.
"""

from typing import Any

from thoth.catalog.base import ANY_TYPE, ItemSpec, RelationSpec, Source, owned_by
from thoth.catalog.items import ITEM_SPECS
from thoth.catalog.relations import RELATION_SPECS

ITEMS: dict[str, type[ItemSpec]] = {spec.type_name: spec for spec in ITEM_SPECS}
RELATIONS: dict[str, type[RelationSpec]] = {spec.type_name: spec for spec in RELATION_SPECS}


def export() -> dict[str, Any]:
    """The whole catalog as JSON-ready data: what catalog.json holds."""
    return {
        "items": {
            name: {
                "description": spec.description(),
                "key_pattern": spec.key_pattern,
                "missing_after_days": _days(spec),
                "schema": spec.model_json_schema(),
            }
            for name, spec in ITEMS.items()
        },
        "relations": {
            name: {
                "description": spec.description(),
                "from": sorted(spec.from_types),
                "to": sorted(spec.to_types),
                "missing_after_days": _days(spec),
                "schema": spec.model_json_schema(),
            }
            for name, spec in RELATIONS.items()
        },
    }


def _days(spec: type[ItemSpec] | type[RelationSpec]) -> int | None:
    return spec.missing_after.days if spec.missing_after else None  # None: 3 scans without it


__all__ = ["ANY_TYPE", "ITEMS", "RELATIONS", "ItemSpec", "RelationSpec", "Source", "export", "owned_by"]
