"""Startup: does every stored row still fit the catalog in this code? Then copy the catalog into its tables.

A new Thoth version runs `problems()` before it reports ready. If a catalog change left rows that don't fit, the new
pod stays not-ready: Kubernetes keeps the old version serving, and the reason is in the readiness answer.
Loads everything into memory - fine at home-lab size (thousands of rows).
"""

import re
from collections import defaultdict
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, select

from thoth.catalog import ANY_TYPE, ITEMS, RELATIONS
from thoth.models import Item, ItemObservation, ItemType, Relation, RelationType


def problems(session: Session) -> list[str]:
    found = []
    items = {item.id: item for item in session.exec(select(Item))}
    slices: dict[Any, dict[str, dict[str, Any]]] = defaultdict(dict)
    for obs in session.exec(select(ItemObservation)):
        slices[obs.item_id][obs.source] = obs.attributes

    for item in items.values():
        where = f"{item.type} {item.key}"
        spec = ITEMS.get(item.type)
        if spec is None:
            found.append(f"{where}: unknown type")
            continue
        if not _matches(spec.key_pattern, item.key):
            found.append(f"{where}: key doesn't match {spec.key_pattern}")
        for source, data in slices[item.id].items():
            found += [f"{where}, from {source}: {e}" for e in _invalid(spec.slice_for(source), data)]
        if item.attributes != spec.merge(slices[item.id]):
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


def _matches(pattern: str, key: str) -> bool:
    return re.fullmatch(pattern, key) is not None


def _invalid(model: Any, data: dict[str, Any]) -> list[str]:
    try:
        model.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc'])) or 'attributes'}: {err['msg']}" for err in e.errors()]
    return []
