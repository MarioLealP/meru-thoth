"""Applying a source's snapshot: its whole picture from one scope, all or nothing (D38, D51, D52, D53).

- Refused whole if anything in it is wrong: unknown type, a key that doesn't match its type, an item outside the
  scope, a field the source doesn't own, or a field it owns but didn't send (= the scanner broke, D51).
- New things become items (planned when Shikigami declares them, active when anything else sees them). The first
  source to report an item owns its name.
- Each source's slice is stored per scope; the item's attributes are the slices merged by field precedence.
- Things this source + scope reported before but not now get a miss; at 3 misses in a row they're on Anubis's list.
  Scanners never set missing or retired - Anubis gives the verdict. Seeing a missing item again makes it active.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text, tuple_
from sqlmodel import Session, col, select

from thoth.catalog import ITEMS, Source
from thoth.catalog.scopes import in_scope, scope_problem
from thoth.db import write_as
from thoth.models import Item, ItemObservation, Status

MISSES_FOR_ANUBIS = 3


class ReportedItem(BaseModel):
    """One thing, as a source sees it."""

    type: str = Field(examples=["account"])
    key: str = Field(examples=["linux/clockmost-desktop/benylin"])
    name: str = Field(examples=["benylin"])
    attributes: dict[str, Any] = Field(examples=[{"email": "lealmariop@gmail.com"}])


class Summary(BaseModel):
    """What a snapshot changed."""

    created: int = Field(examples=[1])
    changed: int = Field(examples=[2])
    unchanged: int = Field(examples=[40])
    missed: int = Field(examples=[1])  # reported before, not this time
    for_anubis: list[str] = Field(examples=[["account linux/clockmost-desktop/olduser"]])  # at 3 misses in a row


class SnapshotRefused(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"snapshot refused: {len(errors)} problems")
        self.errors = errors


def current_slices(observations: Iterable[ItemObservation]) -> dict[str, dict[str, Any]]:
    """Each source's slice of one item; a source seen from several scopes counts its freshest one."""
    slices: dict[str, dict[str, Any]] = {}
    for obs in sorted(observations, key=lambda o: (o.missed_scans == 0, o.last_seen)):
        slices[obs.source] = obs.attributes
    return slices


def problems(source: str, scope: str, items: list[ReportedItem]) -> list[str]:
    if found := scope_problem(source, scope):
        return [found]
    errors, seen = [], set()
    for n, item in enumerate(items):
        where = f"items[{n}] ({item.type} {item.key})"
        spec = ITEMS.get(item.type)
        if spec is None:
            errors.append(f"{where}: unknown type")
            continue
        if (item.type, item.key) in seen:
            errors.append(f"{where}: reported twice")
        seen.add((item.type, item.key))
        if not spec.key_ok(item.key):
            errors.append(f"{where}: key doesn't match {spec.key_pattern}")
        if not in_scope(source, scope, item.type, item.key, item.attributes):
            errors.append(f"{where}: outside scope {scope}")
        try:
            spec.slice_for(source, strict=True).model_validate(item.attributes)
        except ValidationError as e:
            errors += [f"{where}: {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    return errors


def apply(session: Session, source: Source, scope: str, items: list[ReportedItem], now: datetime) -> Summary:
    if errors := problems(source, scope, items):
        raise SnapshotRefused(errors)
    write_as(session, source, f"snapshot from {source}@{scope}")
    # One snapshot per source + scope at a time; others wait for this transaction.
    session.connection().execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{source}@{scope}"})

    declaring = source == Source.SHIKIGAMI  # declares, doesn't see (D40, D53)
    pairs = [(i.type, i.key) for i in items]
    existing = {
        (i.type, i.key): i
        for i in (session.exec(select(Item).where(tuple_(col(Item.type), col(Item.key)).in_(pairs))) if pairs else [])
    }
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
            # Born with its attributes: inserting empty and merging after would leave a fake empty first version.
            item = Item(type=r.type, key=r.key, name=r.name, source=source, first_seen=now, last_seen=now,
                        status=Status.PLANNED if declaring else Status.ACTIVE,
                        attributes=ITEMS[r.type].merge({source: r.attributes}))  # fmt: skip
            session.add(item)
            created += 1
        else:
            before[item.id] = _state(item)
            if not declaring and item.status in (Status.PLANNED, Status.MISSING):
                item.status = Status.ACTIVE
            if item.source == source:
                item.name = r.name
            if not declaring:
                item.last_seen = now
        session.flush()
        obs = mine.get(item.id)
        if obs is None:
            obs = ItemObservation(item_id=item.id, source=source, scope=scope, first_seen=now, last_seen=now)
            session.add(obs)
        obs.attributes, obs.last_seen, obs.missed_scans = r.attributes, now, 0
        reported[item.id] = item

    missed, for_anubis = 0, []
    gone = [o for item_id, o in mine.items() if item_id not in reported]
    gone_items = {i.id: i for i in session.exec(select(Item).where(col(Item.id).in_([o.item_id for o in gone])))}
    for obs in gone:
        obs.missed_scans += 1
        missed += 1
        if obs.missed_scans >= MISSES_FOR_ANUBIS:
            item = gone_items[obs.item_id]
            for_anubis.append(f"{item.type} {item.key}")
    session.flush()

    all_obs: dict[UUID, list[ItemObservation]] = defaultdict(list)
    for obs in session.exec(select(ItemObservation).where(col(ItemObservation.item_id).in_(list(reported)))):
        all_obs[obs.item_id].append(obs)
    for item_id, item in reported.items():
        merged = ITEMS[item.type].merge(current_slices(all_obs[item_id]))
        if merged != item.attributes:
            item.attributes = merged
    session.flush()
    changed = sum(1 for item_id, old in before.items() if old != _state(reported[item_id]))
    return Summary(
        created=created,
        changed=changed,
        unchanged=len(before) - changed,
        missed=missed,
        for_anubis=sorted(for_anubis),
    )


def _state(item: Item) -> tuple[str, str, dict[str, Any]]:
    return item.name, item.status, item.attributes
