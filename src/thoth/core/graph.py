"""Walking the web of relations - Thoth builds it from one-hop links; callers only see the result (D42, D55).

impact = who depends on this (arrows backwards); dependencies = what this needs (arrows forwards). One recursive
query: the walk carries the path it took, never follows a link back onto that path (the loop guard), and reports
such links as cycles. Each item comes back once, at its shortest distance. Retired links are not walked.
"""

from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.models import Item, Relation, Status


class Direction(StrEnum):
    """impact: who depends on this (links backwards). dependencies: what this needs (links forwards)."""

    IMPACT = "impact"
    DEPENDENCIES = "dependencies"


class Step(BaseModel):
    """An item the walk reached, at its shortest distance."""

    model_config = ConfigDict(frozen=True)

    item_id: UUID
    depth: int
    relation: Relation  # the link the walk arrived by


class Walk(BaseModel):
    """A finished walk: what it reached, the loops it met, and the items to name them."""

    model_config = ConfigDict(frozen=True)

    steps: list[Step]
    cycles: list[Relation]  # links that close a loop - reported, not followed
    items: dict[UUID, Item]  # every item mentioned, for naming


async def walk(session: AsyncSession, root: UUID, direction: Direction, max_depth: int) -> Walk:
    """Walk the relations from `root` in `direction`, at most `max_depth` links away."""
    r = sa.table(Relation.__tablename__, sa.column("id"), sa.column("from_item"), sa.column("to_item"),
                 sa.column("status"))  # fmt: skip
    near, far = (r.c.to_item, r.c.from_item) if direction == Direction.IMPACT else (r.c.from_item, r.c.to_item)
    live = r.c.status != Status.RETIRED.value
    root_value = sa.literal(root, sa.Uuid)

    first = sa.select(
        far.label("item_id"),
        r.c.id.label("relation_id"),
        sa.literal(1).label("depth"),
        sa.cast(array([root_value, far]), ARRAY(sa.Uuid)).label("path"),
        (far == root_value).label("cycle"),
    ).where(near == root_value, live)
    steps = first.cte("walk", recursive=True)
    following = (
        sa.select(
            far,
            r.c.id,
            steps.c.depth + 1,
            sa.func.array_append(steps.c.path, far),
            far == sa.any_(steps.c.path),
        )
        .join(steps, near == steps.c.item_id)
        .where(live, steps.c.depth < max_depth, sa.not_(steps.c.cycle))
    )
    steps = steps.union_all(following)
    rows = await (await session.connection()).execute(
        sa.select(steps.c.item_id, steps.c.relation_id, steps.c.depth, steps.c.cycle)
    )

    shortest: dict[UUID, tuple[int, UUID]] = {}
    cycle_ids: set[UUID] = set()
    for item_id, relation_id, depth, cycle in rows:
        if cycle:
            cycle_ids.add(relation_id)
        elif item_id not in shortest or depth < shortest[item_id][0]:
            shortest[item_id] = (depth, relation_id)

    relation_ids = {rid for _, rid in shortest.values()} | cycle_ids
    relations = {x.id: x for x in await session.exec(select(Relation).where(col(Relation.id).in_(relation_ids)))}
    item_ids = {root} | {i for x in relations.values() for i in (x.from_item, x.to_item)}
    items = {x.id: x for x in await session.exec(select(Item).where(col(Item.id).in_(item_ids)))}
    ordered = sorted(shortest.items(), key=lambda kv: (kv[1][0], items[kv[0]].type, items[kv[0]].key))
    return Walk(
        steps=[Step(item_id=item_id, depth=depth, relation=relations[rid]) for item_id, (depth, rid) in ordered],
        cycles=sorted((relations[rid] for rid in cycle_ids), key=lambda x: (x.type, str(x.id))),
        items=items,
    )
