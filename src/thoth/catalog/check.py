"""Startup: does every stored row still fit the catalog in this code? Then copy the catalog into its tables.

A new Thoth version runs `problems()` before it reports ready. If a catalog change left rows that don't fit, the new
pod stays not-ready: Kubernetes keeps the old version serving, and the reason is in the readiness answer.
Loads everything into memory - fine at home-lab size (thousands of rows).
"""

from collections import defaultdict
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from thoth.catalog import ANY_TYPE, ITEMS, RELATIONS
from thoth.models import Item, ItemObservation, ItemType, Relation, RelationType
from thoth.snapshots import current_slices


def problems(session: Session) -> list[str]:
    found = []
    items = {item.id: item for item in session.exec(select(Item))}
    observations: dict[Any, list[ItemObservation]] = defaultdict(list)
    for obs in session.exec(select(ItemObservation)):
        observations[obs.item_id].append(obs)

    for item in items.values():
        where = f"{item.type} {item.key}"
        spec = ITEMS.get(item.type)
        if spec is None:
            found.append(f"{where}: unknown type")
            continue
        if not spec.key_ok(item.key):
            found.append(f"{where}: key doesn't match {spec.key_pattern}")
        for obs in observations[item.id]:
            errors = _invalid(spec.slice_for(obs.source), obs.attributes)
            found += [f"{where}, from {obs.source}@{obs.scope}: {e}" for e in errors]
        if item.attributes != spec.merge(current_slices(observations[item.id])):
            found.append(f"{where}: attributes don't match the merged observations")

    for rel in session.exec(select(Relation)):
        a, b = items[rel.from_item], items[rel.to_item]
        where = f"{a.type} {a.key} {rel.type} {b.type} {b.key}"
        spec = RELATIONS.get(rel.type)
        if spec is None:
            found.append(f"{where}: unknown relation type")
            continue
        if not {ANY_TYPE, a.type} & spec.from_types or not {ANY_TYPE, b.type} & spec.to_types:
            found.append(f"{where}: {rel.type} doesn't allow these item types")
        found += [f"{where}: {e}" for e in _invalid(spec.slice_for(rel.source), rel.attributes)]
    return found


def sync(session: Session) -> None:
    """Upsert the catalog into item_type / relation_type (types gone from code stay until nothing uses them)."""
    for name, spec in ITEMS.items():
        row = {"name": name, "description": spec.description(), "key_pattern": spec.key_pattern,
               "attribute_schema": spec.model_json_schema()}  # fmt: skip
        session.exec(insert(ItemType).values(row).on_conflict_do_update(index_elements=["name"], set_=row))
    for name, spec in RELATIONS.items():
        row = {"name": name, "description": spec.description(), "from_types": sorted(spec.from_types),
               "to_types": sorted(spec.to_types), "attribute_schema": spec.model_json_schema()}  # fmt: skip
        session.exec(insert(RelationType).values(row).on_conflict_do_update(index_elements=["name"], set_=row))


def _invalid(model: Any, data: dict[str, Any]) -> list[str]:
    try:
        model.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc'])) or 'attributes'}: {err['msg']}" for err in e.errors()]
    return []
