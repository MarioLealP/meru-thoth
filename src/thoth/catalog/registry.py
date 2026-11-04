"""Every item and relation type by name, and their export to JSON."""

from typing import Any

from thoth.catalog.base import ItemSpec, RelationSpec
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
