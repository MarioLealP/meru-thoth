"""Startup: does every stored row still fit the catalog in this code? Then copy the catalog into its tables.

A new Thoth version runs `problems()` before it reports ready. If a catalog change left rows that don't fit, the new
pod stays not-ready: Kubernetes keeps the old version serving, and the reason is in the readiness answer.
Loads everything into memory - fine at home-lab size (thousands of rows).
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.catalog import ANY_TYPE, ITEMS, RELATIONS
from thoth.catalog.base import Spec
from thoth.models import Item, ItemObservation, ItemType, Relation, RelationObservation, RelationType
from thoth.snapshots import current_slices


async def problems(session: AsyncSession) -> list[str]:
    """Everything stored that doesn't fit the catalog in this code, one line each."""
    items = {item.id: item for item in await session.exec(select(Item))}
    item_observations: dict[Any, list[ItemObservation]] = defaultdict(list)
    for obs in await session.exec(select(ItemObservation)):
        item_observations[obs.item_id].append(obs)
    rel_observations: dict[Any, list[RelationObservation]] = defaultdict(list)
    for obs in await session.exec(select(RelationObservation)):
        rel_observations[obs.relation_id].append(obs)

    found = []
    for item in items.values():
        found += _item_problems(item, item_observations[item.id])
    for rel in await session.exec(select(Relation)):
        found += _relation_problems(rel, items[rel.from_item], items[rel.to_item], rel_observations[rel.id])
    return found


def _item_problems(item: Item, observations: list[ItemObservation]) -> list[str]:
    where = f"{item.type} {item.key}"
    spec = ITEMS.get(item.type)
    if spec is None:
        return [f"{where}: unknown type"]
    found = [] if spec.key_ok(item.key) else [f"{where}: key doesn't match {spec.key_pattern}"]
    return found + _slice_problems(where, spec, item.attributes, observations)


def _relation_problems(rel: Relation, a: Item, b: Item, observations: list[RelationObservation]) -> list[str]:
    where = f"{a.type} {a.key} {rel.type} {b.type} {b.key}"
    spec = RELATIONS.get(rel.type)
    if spec is None:
        return [f"{where}: unknown relation type"]
    found = []
    if not {ANY_TYPE, a.type} & spec.from_types or not {ANY_TYPE, b.type} & spec.to_types:
        found.append(f"{where}: {rel.type} doesn't allow these item types")
    return found + _slice_problems(where, spec, rel.attributes, observations)


def _slice_problems(
    where: str,
    spec: type[Spec],
    attributes: dict[str, Any],
    observations: Sequence[ItemObservation | RelationObservation],
) -> list[str]:
    """Each stored slice must still fit its source's fields, and the merged slices must equal `attributes`."""
    found = []
    for obs in observations:
        errors = _invalid(spec.slice_for(obs.source), obs.attributes)
        found += [f"{where}, from {obs.source}@{obs.scope}: {e}" for e in errors]
    if attributes != spec.merge(current_slices(observations)):
        found.append(f"{where}: attributes don't match the merged observations")
    return found


async def sync(session: AsyncSession) -> None:
    """Upsert the catalog into item_type / relation_type (types gone from code stay until nothing uses them)."""
    for name, spec in ITEMS.items():
        row = {"name": name, "description": spec.description(), "key_pattern": spec.key_pattern,
               "attribute_schema": spec.model_json_schema()}  # fmt: skip
        await session.exec(insert(ItemType).values(row).on_conflict_do_update(index_elements=["name"], set_=row))
    for name, spec in RELATIONS.items():
        row = {"name": name, "description": spec.description(), "from_types": sorted(spec.from_types),
               "to_types": sorted(spec.to_types), "attribute_schema": spec.model_json_schema()}  # fmt: skip
        await session.exec(insert(RelationType).values(row).on_conflict_do_update(index_elements=["name"], set_=row))


def _invalid(model: Any, data: dict[str, Any]) -> list[str]:
    try:
        model.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc'])) or 'attributes'}: {err['msg']}" for err in e.errors()]
    return []
