"""Item snapshots: phase 1 of a scan (D54).

- Refused whole if anything in it is wrong: unknown type, a key that doesn't match its type, an item outside the
  scope, a field the source doesn't own, or a field it owns but didn't send (= the scanner broke, D51), or a new item
  of a type the source may only confirm (D100).
- New things become items (planned when Shikigami declares them, active when anything else sees them). The first
  source to report an item owns its name.
- Each source's slice is stored per scope; the item's attributes are the slices merged by field precedence.
- Things this source + scope reported before but not in this scan get a miss; enough misses (or, for types that come
  and go, enough time unseen) put them on Anubis's list. Scanners never set missing or retired (D53).
"""

from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import tuple_
from sqlmodel import Session, col, select

from thoth.catalog import ITEMS, Source
from thoth.catalog.scopes import CONFIRMS, in_scope, scope_problem
from thoth.models import Item, ItemObservation, Status
from thoth.snapshots.common import (
    SnapshotRefusedError,
    Summary,
    begin,
    current_slices,
    due_for_anubis,
    missed,
    observed,
    seen,
)


class ReportedItem(BaseModel):
    """One thing, as a source sees it."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(examples=["account"])
    key: str = Field(examples=["linux/clockmost-desktop/benylin"])
    name: str = Field(examples=["benylin"])
    attributes: dict[str, Any] = Field(examples=[{"email": "lealmariop@gmail.com"}])


def problems(source: str, scope: str, items: list[ReportedItem]) -> list[str]:
    """What's wrong with an item snapshot, without looking at the database."""
    if found := scope_problem(source, scope):
        return [found]
    errors, seen_keys = [], set()
    for n, item in enumerate(items):
        where = f"items[{n}] ({item.type} {item.key})"
        spec = ITEMS.get(item.type)
        if spec is None:
            errors.append(f"{where}: unknown type")
            continue
        if (item.type, item.key) in seen_keys:
            errors.append(f"{where}: reported twice")
        seen_keys.add((item.type, item.key))
        if not spec.key_ok(item.key):
            errors.append(f"{where}: key doesn't match {spec.key_pattern}")
        if not in_scope(source, scope, item.type, item.key, item.attributes):
            errors.append(f"{where}: outside scope {scope}")
        try:
            spec.slice_for(source, strict=True).model_validate(item.attributes)
        except ValidationError as e:
            errors += [f"{where}: {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    return errors


def apply_items(
    session: Session, actor: str, scan_id: str, source: Source, scope: str, items: list[ReportedItem], now: datetime
) -> Summary:
    """Apply one source's item snapshot in the session; the caller commits."""
    if errors := problems(source, scope, items):
        raise SnapshotRefusedError(errors)
    existing = _existing(session, items)
    _refuse_undeclared(source, items, existing)
    begin(session, actor, source, scope, "items")

    declaring = source == Source.SHIKIGAMI  # declares, doesn't see (D40, D53)
    mine = {
        o.item_id: o
        for o in session.exec(
            select(ItemObservation).where(ItemObservation.source == source, ItemObservation.scope == scope)
        )
    }

    created, reported, before = 0, {}, {}
    for r in items:
        item = existing.get((r.type, r.key))
        if item is None:
            item = _new_item(r, source, now, declaring)
            session.add(item)
            created += 1
        else:
            before[item.id] = _state(item)
            _seen_again(item, r, source, now, declaring)
        session.flush()
        obs = mine.get(item.id)
        if obs is None:
            obs = ItemObservation(item_id=item.id, source=source, scope=scope, first_seen=now, last_seen=now,
                                  last_scan=scan_id)  # fmt: skip
            session.add(obs)
        observed(obs, r.attributes, now, scan_id)
        reported[item.id] = item

    gone = [o for item_id, o in mine.items() if item_id not in reported]
    misses, for_anubis = _count_misses(session, gone, scan_id, now)
    _merge_slices(session, reported)
    changed = sum(1 for item_id, old in before.items() if old != _state(reported[item_id]))
    return Summary(
        created=created,
        changed=changed,
        unchanged=len(before) - changed,
        missed=misses,
        for_anubis=sorted(for_anubis),
    )


def _existing(session: Session, items: list[ReportedItem]) -> dict[tuple[str, str], Item]:
    pairs = [(i.type, i.key) for i in items]
    if not pairs:
        return {}
    return {
        (i.type, i.key): i for i in session.exec(select(Item).where(tuple_(col(Item.type), col(Item.key)).in_(pairs)))
    }


def _refuse_undeclared(source: Source, items: list[ReportedItem], existing: dict[tuple[str, str], Item]) -> None:
    """A source that may only confirm a type can't bring a new item of it (D100)."""
    confirm_only = CONFIRMS.get(source, frozenset())
    undeclared = [(n, r) for n, r in enumerate(items) if r.type in confirm_only and (r.type, r.key) not in existing]
    if undeclared:
        raise SnapshotRefusedError(
            [
                f"items[{n}] ({r.type} {r.key}): not declared - {source} can confirm one, never create it"
                for n, r in undeclared
            ]
        )


def _new_item(r: ReportedItem, source: Source, now: datetime, declaring: bool) -> Item:
    """Born with its attributes: inserting empty and merging after would leave a fake empty first version."""
    return Item(type=r.type, key=r.key, name=r.name, source=source, first_seen=now, last_seen=now,
                status=Status.PLANNED if declaring else Status.ACTIVE,
                attributes=ITEMS[r.type].merge({source: r.attributes}))  # fmt: skip


def _seen_again(item: Item, r: ReportedItem, source: Source, now: datetime, declaring: bool) -> None:
    seen(item, declaring)
    if item.source == source:
        item.name = r.name
    if not declaring:
        item.last_seen = now


def _count_misses(session: Session, gone: list[ItemObservation], scan_id: str, now: datetime) -> tuple[int, list[str]]:
    """Count a miss for each thing not in this scan; returns the misses and what's now due for Anubis."""
    gone_items = {i.id: i for i in session.exec(select(Item).where(col(Item.id).in_([o.item_id for o in gone])))}
    misses, for_anubis = 0, []
    for obs in gone:
        misses += missed(obs, scan_id)
        item = gone_items[obs.item_id]
        if due_for_anubis(ITEMS[item.type], obs, now):
            for_anubis.append(f"{item.type} {item.key}")
    session.flush()
    return misses, for_anubis


def _merge_slices(session: Session, reported: dict[UUID, Item]) -> None:
    """Set each reported item's attributes from all its sources' slices."""
    all_obs: dict[UUID, list[ItemObservation]] = defaultdict(list)
    for obs in session.exec(select(ItemObservation).where(col(ItemObservation.item_id).in_(list(reported)))):
        all_obs[obs.item_id].append(obs)
    for item_id, item in reported.items():
        merged = ITEMS[item.type].merge(current_slices(all_obs[item_id]))
        if merged != item.attributes:
            item.attributes = merged
    session.flush()


def _state(item: Item) -> tuple[str, str, dict[str, Any]]:
    return item.name, item.status, item.attributes
