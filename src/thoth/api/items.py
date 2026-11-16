"""Routes for items: renames, retire steps, reads, the web around an item and its history."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from thoth.api.deps import CallerDep, ItemDep, Reader, SessionDep
from thoth.core import history
from thoth.core.graph import Direction, walk
from thoth.core.renames import Rename, RenameRefusedError, rename
from thoth.core.retire_steps import RetireStep, StepRefusedError, retire_step
from thoth.models import Item, Relation, Status, table_of

router = APIRouter(tags=["items"])


class ItemOut(BaseModel):
    """An item as it is now. `changed_*` say who made this version and why; earlier versions are its history."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(examples=["7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11"])
    type: str = Field(examples=["host"])
    key: str = Field(examples=["clockmost-ai"])
    name: str = Field(examples=["clockmost-ai"])
    status: Status = Field(examples=["active"])
    attributes: dict[str, Any] = Field(examples=[{"kind": "physical", "os": "AlmaLinux 10.2"}])
    source: str = Field(examples=["host-scan"])
    first_seen: datetime = Field(examples=["2026-10-01T12:00:00Z"])
    last_seen: datetime = Field(examples=["2026-10-29T12:00:00Z"])
    changed_at: datetime | None = Field(examples=["2026-10-29T12:00:00Z"])
    changed_by: str | None = Field(examples=["manual"])
    change_reason: str | None = Field(examples=["rename nitro5 -> clockmost-ai: the Nitro 5 became the AI node"])


_ITEM_EXAMPLE = {
    "id": "7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11",
    "type": "host",
    "key": "clockmost-ai",
    "name": "clockmost-ai",
    "status": "active",
    "attributes": {"kind": "physical", "os": "AlmaLinux 10.2"},
    "source": "host-scan",
    "first_seen": "2026-10-01T12:00:00Z",
    "last_seen": "2026-10-29T12:00:00Z",
    "changed_at": "2026-10-29T12:00:00Z",
    "changed_by": "manual",
    "change_reason": "rename nitro5 -> clockmost-ai: the Nitro 5 became the AI node",
}
_RENAME_EXAMPLE = {
    "type": "host",
    "key": "nitro5",
    "new_key": "clockmost-ai",
    "new_name": "clockmost-ai",
    "reason": "the Nitro 5 became the AI node",
}


@router.post(
    "/renames",
    operation_id="rename_item",
    status_code=200,
    openapi_extra={
        "requestBody": {
            "description": "Which item, its new key, and why.",
            "content": {"application/json": {"example": _RENAME_EXAMPLE}},
        }
    },
    responses={200: {"content": {"application/json": {"example": _ITEM_EXAMPLE}}}},
)
async def rename_item(request: Rename, caller: CallerDep, session: SessionDep) -> ItemOut:
    """Give an item a new key: same item, same relations; the old key stays in its history."""
    try:
        item = await rename(session, request, caller)
        await session.commit()
    except RenameRefusedError as refused:
        raise HTTPException(refused.status, refused.detail) from refused
    except IntegrityError as e:  # someone took the key in the meantime
        raise HTTPException(409, f"{request.type} {request.new_key} already exists") from e
    return ItemOut.model_validate(item, from_attributes=True)


_RETIRE_STEP_EXAMPLE = {
    "app": "kitsune",
    "status": "decommissioning",
    "reason": "retire pass started: Meru/requests#13",
}


@router.post(
    "/retire-steps",
    operation_id="retire_step",
    status_code=200,
    openapi_extra={
        "requestBody": {
            "description": "Which app, the step it reached, and why.",
            "content": {"application/json": {"example": _RETIRE_STEP_EXAMPLE}},
        }
    },
    responses={200: {"content": {"application/json": {"example": _ITEM_EXAMPLE}}}},
)
async def post_retire_step(request: RetireStep, caller: CallerDep, session: SessionDep) -> ItemOut:
    """Shikigami's retire pass (D85).

    An app goes decommissioning when the pass starts and retained when its last step is done; planned again when its
    request is restored (D106). Needs thoth.retire. A step already reached is answered as it is.
    """
    try:
        item = await retire_step(session, request, caller)
        await session.commit()
    except StepRefusedError as refused:
        raise HTTPException(refused.status, refused.detail) from refused
    return ItemOut.model_validate(item, from_attributes=True)


def _ref_example(key: str, n: int) -> dict[str, Any]:
    return {"id": f"7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a1{n}", "type": "app", "key": key, "name": key, "status": "active"}


_TEMPORAL_DB, _TEMPORAL, _SHIKIGAMI = (
    _ref_example(k, n) for n, k in enumerate(("temporal-db", "temporal", "shikigami"))
)
_CYCLE_EXAMPLE = {"type": "uses", "from": _TEMPORAL, "to": _SHIKIGAMI}
_WEB_EXAMPLE = {
    "root": _TEMPORAL,
    "depth": 10,
    "impact": [{"item": _SHIKIGAMI, "depth": 1, "via": {"type": "uses", "from": _SHIKIGAMI, "to": _TEMPORAL}}],
    "dependencies": [
        {"item": _TEMPORAL_DB, "depth": 1, "via": {"type": "uses", "from": _TEMPORAL, "to": _TEMPORAL_DB}}
    ],
    "cycles": [],
}
_WEB_RESPONSE: dict[int | str, dict[str, Any]] = {200: {"content": {"application/json": {"example": _WEB_EXAMPLE}}}}


class ItemRef(BaseModel):
    """Enough to name an item."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(examples=["7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11"])
    type: str = Field(examples=["app"])
    key: str = Field(examples=["temporal"])
    name: str = Field(examples=["Temporal"])
    status: Status = Field(examples=["active"])


class LinkOut(BaseModel):
    """A link: `from` depends on `to`."""

    model_config = ConfigDict(frozen=True, serialize_by_alias=True)

    type: str = Field(examples=["uses"])
    from_: ItemRef = Field(alias="from")
    to: ItemRef


class Step(BaseModel):
    """An item the walk reached, how far from the start, and the link it arrived by."""

    model_config = ConfigDict(frozen=True)

    item: ItemRef
    depth: int = Field(examples=[1])
    via: LinkOut


class WebOut(BaseModel):
    """The web around one item, built from one-hop links.

    What depends on it (`impact`) and what it needs (`dependencies`). Each item once per side, at its shortest distance,
    with the link it arrived by; links that would loop back are listed under `cycles` instead of followed.
    """

    model_config = ConfigDict(frozen=True)

    root: ItemRef
    depth: int = Field(examples=[10])
    impact: list[Step] = Field(examples=[_WEB_EXAMPLE["impact"]])
    dependencies: list[Step] = Field(examples=[_WEB_EXAMPLE["dependencies"]])
    cycles: list[LinkOut] = Field(examples=[[_CYCLE_EXAMPLE]])


def _ref(item: Item) -> ItemRef:
    return ItemRef.model_validate(item, from_attributes=True)


def _link(relation: Relation, items: dict[UUID, Item]) -> LinkOut:
    return LinkOut.model_validate(
        {"type": relation.type, "from": _ref(items[relation.from_item]), "to": _ref(items[relation.to_item])}
    )


@router.get(
    "/items",
    operation_id="list_items",
    status_code=200,
    responses={200: {"content": {"application/json": {"example": [_ITEM_EXAMPLE]}}}},
)
async def list_items(
    _: Reader,
    session: SessionDep,
    type: Annotated[str | None, Query(description="Only this item type.", examples=["host"])] = None,
    key: Annotated[str | None, Query(description="Only this exact key.", examples=["clockmost"])] = None,
    status: Annotated[Status | None, Query(description="Only this status.", examples=["active"])] = None,
    source: Annotated[str | None, Query(description="Only items first reported by this source.")] = None,
    as_of: Annotated[
        datetime | None,
        Query(
            description="Every item as it was at this moment (from history), instead of now.",
            examples=["2026-10-01T12:00:00Z"],
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="At most this many.")] = 100,
    offset: Annotated[int, Query(ge=0, description="Skip this many (for paging).")] = 0,
) -> list[ItemOut]:
    """Items, filtered, ordered by type then key. Keys contain slashes, so look one up with `?type=&key=`."""
    rows = history.as_of(as_of) if as_of is not None else table_of(Item)
    query = sa.select(rows)
    for column, value in (("type", type), ("key", key), ("status", status), ("source", source)):
        if value is not None:
            query = query.where(rows.c[column] == value)
    query = query.order_by(rows.c.type, rows.c.key).limit(limit).offset(offset)
    result = await (await session.connection()).execute(query)
    return [ItemOut.model_validate(dict(row._mapping)) for row in result]


@router.get(
    "/items/{item_id}",  # ruff: ignore[fast-api-unused-path-parameter] - the ItemDep dependency takes the path parameter
    operation_id="get_item",
    status_code=200,
    responses={200: {"content": {"application/json": {"example": _ITEM_EXAMPLE}}}},
)
async def get_item(_: Reader, item: ItemDep) -> ItemOut:
    """One item as it is now."""
    return ItemOut.model_validate(item, from_attributes=True)


class Side(StrEnum):
    """Which side of the web to walk."""

    BOTH = "both"
    IMPACT = "impact"
    DEPENDENCIES = "dependencies"


DepthQuery = Annotated[int, Query(ge=1, le=50, description="How many links away to go; 1 = direct neighbours only.")]
SideQuery = Annotated[Side, Query(description="Both sides, or only one.")]


@router.get(
    "/items/{item_id}/web",  # ruff: ignore[fast-api-unused-path-parameter] - the ItemDep dependency takes the path parameter
    operation_id="get_web",
    status_code=200,
    responses=_WEB_RESPONSE,
)
async def get_web(
    _: Reader, root: ItemDep, session: SessionDep, depth: DepthQuery = 10, direction: SideQuery = Side.BOTH
) -> WebOut:
    """What depends on this item (what breaks if it goes) and what it needs to work."""
    sides = {d: await walk(session, root.id, d, depth) for d in Direction if direction in (Side.BOTH, d.value)}
    items = {root.id: root} | {k: v for w in sides.values() for k, v in w.items.items()}
    cycles = {c.id: c for w in sides.values() for c in w.cycles}  # a loop shows up on both sides: once

    def steps(d: Direction) -> list[Step]:
        found = sides[d].steps if d in sides else []
        return [Step(item=_ref(items[s.item_id]), depth=s.depth, via=_link(s.relation, items)) for s in found]

    return WebOut(
        root=_ref(root),
        depth=depth,
        impact=steps(Direction.IMPACT),
        dependencies=steps(Direction.DEPENDENCIES),
        cycles=[_link(c, items) for c in sorted(cycles.values(), key=lambda c: (c.type, str(c.id)))],
    )


class VersionOut(ItemOut):
    """One version of an item: what it looked like from `valid_from` until `valid_to` (None: still current)."""

    valid_from: datetime | None = Field(examples=["2026-10-01T12:00:00Z"])
    valid_to: datetime | None = Field(default=None, examples=["2026-10-29T12:00:00Z"])


_HISTORY_EXAMPLE = [
    {**_ITEM_EXAMPLE, "valid_from": "2026-10-29T12:00:00Z", "valid_to": None},
    {**_ITEM_EXAMPLE, "key": "nitro5", "name": "nitro5", "changed_by": "host-scan",
     "change_reason": "snapshot from host-scan@nitro5", "valid_from": "2026-10-01T12:00:00Z",
     "valid_to": "2026-10-29T12:00:00Z"},
]  # fmt: skip


@router.get(
    "/items/{item_id}/history",  # ruff: ignore[fast-api-unused-path-parameter] - the ItemDep dependency takes the path parameter
    operation_id="get_item_history",
    status_code=200,
    responses={200: {"content": {"application/json": {"example": _HISTORY_EXAMPLE}}}},
)
async def get_item_history(_: Reader, item: ItemDep, session: SessionDep) -> list[VersionOut]:
    """Every version of the item, newest first, each with who made it and why."""
    return [VersionOut.model_validate(v) for v in await history.versions(session, item)]
