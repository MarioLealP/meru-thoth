"""Relation snapshots: phase 2 of a scan, after every source's items (D54).

Links are named by their ends' type + key. The same rules as items - strict slices, per-source + scope
observations, misses, Anubis - with two of their own: a link is in scope if its from (dependent) end is, and a link
whose end doesn't exist (that end's source failed this scan) is skipped and listed, not refused. The next scan sends
the full picture again, so it heals by itself.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import tuple_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.catalog import ANY_TYPE, ITEMS, RELATIONS, RelationSpec, Source
from thoth.catalog.scopes import in_scope, scope_problem
from thoth.models import Item, Relation, RelationObservation, Status
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


class ReportedEnd(BaseModel):
    """One end of a link: the item's type and key."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(examples=["account"])
    key: str = Field(examples=["linux/clockmost-desktop/benylin"])

    def __str__(self) -> str:
        """The end as reports name it: type, then key."""
        return f"{self.type} {self.key}"


class ReportedRelation(BaseModel):
    """One link, as a source sees it: `from` depends on `to`."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    type: str = Field(examples=["member-of"])
    from_: ReportedEnd = Field(alias="from")
    to: ReportedEnd = Field(examples=[{"type": "group", "key": "linux/clockmost-desktop/wheel"}])
    attributes: dict[str, Any] = Field(examples=[{}])

    def __str__(self) -> str:
        """The link as reports name it: from, type, to."""
        return f"{self.from_} {self.type} {self.to}"


def problems(source: str, scope: str, relations: list[ReportedRelation]) -> list[str]:
    """What's wrong without looking at the database. The scope check needs the from-item, so it comes later."""
    if found := scope_problem(source, scope):
        return [found]
    errors, seen_links = [], set()
    for n, rel in enumerate(relations):
        where = f"relations[{n}] ({rel})"
        spec = RELATIONS.get(rel.type)
        if spec is None:
            errors.append(f"{where}: unknown relation type")
            continue
        errors += _end_problems(where, rel, spec)
        if (rel.from_.type, rel.from_.key) == (rel.to.type, rel.to.key):
            errors.append(f"{where}: a link to itself")
        if (link := (rel.type, str(rel.from_), str(rel.to))) in seen_links:
            errors.append(f"{where}: reported twice")
        seen_links.add(link)
        try:
            spec.slice_for(source, strict=True).model_validate(rel.attributes)
        except ValidationError as e:
            errors += [f"{where}: {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    return errors


def _end_problems(where: str, rel: ReportedRelation, spec: type[RelationSpec]) -> list[str]:
    errors = []
    for end, allowed in ((rel.from_, spec.from_types), (rel.to, spec.to_types)):
        item_spec = ITEMS.get(end.type)
        if item_spec is None:
            errors.append(f"{where}: unknown item type {end.type}")
        elif not item_spec.key_ok(end.key):
            errors.append(f"{where}: key {end.key} doesn't match {item_spec.key_pattern}")
        elif not {ANY_TYPE, end.type} & allowed:
            errors.append(f"{where}: {rel.type} doesn't allow {end.type} at that end")
    return errors


async def apply_relations(
    session: AsyncSession,
    actor: str,
    scan_id: str,
    source: Source,
    scope: str,
    relations: list[ReportedRelation],
    now: datetime,
) -> Summary:
    """Apply one source's relation snapshot in the session; the caller commits."""
    if errors := problems(source, scope, relations):
        raise SnapshotRefusedError(errors)
    resolved, skipped = await _resolve(session, source, scope, relations)
    await begin(session, actor, source, scope, "relations")

    declaring = source == Source.SHIKIGAMI
    existing = await _existing(session, resolved)
    mine = {
        o.relation_id: o
        for o in await session.exec(
            select(RelationObservation).where(RelationObservation.source == source, RelationObservation.scope == scope)
        )
    }

    created, reported, before = 0, {}, {}
    for rel, a, b in resolved:
        row = existing.get((rel.type, a.id, b.id))
        if row is None:
            row = Relation(type=rel.type, from_item=a.id, to_item=b.id, source=source, first_seen=now, last_seen=now,
                           status=Status.PLANNED if declaring else Status.ACTIVE,
                           attributes=RELATIONS[rel.type].merge({source: rel.attributes}))  # fmt: skip
            session.add(row)
            created += 1
        else:
            before[row.id] = (row.status, row.attributes)
            seen(row, declaring)
            if not declaring:
                row.last_seen = now
        await session.flush()
        obs = mine.get(row.id)
        if obs is None:
            obs = RelationObservation(relation_id=row.id, source=source, scope=scope, first_seen=now, last_seen=now,
                                      last_scan=scan_id)  # fmt: skip
            session.add(obs)
        observed(obs, rel.attributes, now, scan_id)
        reported[row.id] = row

    gone = [o for rel_id, o in mine.items() if rel_id not in reported]
    misses, for_anubis = await _count_misses(session, gone, scan_id, now)
    await _merge_slices(session, reported)
    changed = sum(1 for rel_id, old in before.items() if old != (reported[rel_id].status, reported[rel_id].attributes))
    return Summary(
        created=created,
        changed=changed,
        unchanged=len(before) - changed,
        missed=misses,
        for_anubis=sorted(for_anubis),
        skipped=skipped,
    )


async def _resolve(
    session: AsyncSession, source: Source, scope: str, relations: list[ReportedRelation]
) -> tuple[list[tuple[ReportedRelation, Item, Item]], list[str]]:
    """Find both ends of every link: links with both, and the ones skipped. A from end outside the scope refuses all."""
    ends = {(e.type, e.key) for r in relations for e in (r.from_, r.to)}
    items = {
        (i.type, i.key): i
        for i in (
            await session.exec(select(Item).where(tuple_(col(Item.type), col(Item.key)).in_(ends))) if ends else []
        )
    }
    resolved, skipped, errors = [], [], []
    for n, rel in enumerate(relations):
        a, b = items.get((rel.from_.type, rel.from_.key)), items.get((rel.to.type, rel.to.key))
        if a is None or b is None:
            missing_end = rel.from_ if a is None else rel.to
            skipped.append(f"{rel} (no item {missing_end} yet)")
            continue
        if not in_scope(source, scope, a.type, a.key, a.attributes):
            errors.append(f"relations[{n}] ({rel}): its from end is outside scope {scope}")
        resolved.append((rel, a, b))
    if errors:
        raise SnapshotRefusedError(errors)
    return resolved, skipped


async def _existing(
    session: AsyncSession, resolved: list[tuple[ReportedRelation, Item, Item]]
) -> dict[tuple[str, UUID, UUID], Relation]:
    triples = [(rel.type, a.id, b.id) for rel, a, b in resolved]
    if not triples:
        return {}
    rows = await session.exec(
        select(Relation).where(tuple_(col(Relation.type), col(Relation.from_item), col(Relation.to_item)).in_(triples))
    )
    return {(r.type, r.from_item, r.to_item): r for r in rows}


async def _count_misses(
    session: AsyncSession, gone: list[RelationObservation], scan_id: str, now: datetime
) -> tuple[int, list[str]]:
    """Count a miss for each link not in this scan; returns the misses and what's now due for Anubis."""
    gone_rows = {
        r.id: r for r in await session.exec(select(Relation).where(col(Relation.id).in_([o.relation_id for o in gone])))
    }
    names = await _names(session, gone_rows.values())
    misses, for_anubis = 0, []
    for obs in gone:
        misses += missed(obs, scan_id)
        row = gone_rows[obs.relation_id]
        if due_for_anubis(RELATIONS[row.type], obs, now):
            for_anubis.append(f"{names[row.from_item]} {row.type} {names[row.to_item]}")
    await session.flush()
    return misses, for_anubis


async def _merge_slices(session: AsyncSession, reported: dict[UUID, Relation]) -> None:
    """Set each reported link's attributes from all its sources' slices."""
    all_obs: dict[UUID, list[RelationObservation]] = defaultdict(list)
    for obs in await session.exec(
        select(RelationObservation).where(col(RelationObservation.relation_id).in_(list(reported)))
    ):
        all_obs[obs.relation_id].append(obs)
    for rel_id, row in reported.items():
        merged = RELATIONS[row.type].merge(current_slices(all_obs[rel_id]))
        if merged != row.attributes:
            row.attributes = merged
    await session.flush()


async def _names(session: AsyncSession, rows: Any) -> dict[UUID, str]:
    ids = {i for r in rows for i in (r.from_item, r.to_item)}
    return (
        {i.id: f"{i.type} {i.key}" for i in await session.exec(select(Item).where(col(Item.id).in_(ids)))}
        if ids
        else {}
    )
