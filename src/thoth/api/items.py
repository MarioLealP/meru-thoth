from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from thoth import history
from thoth.api.deps import CallerDep, Reader, get_engine
from thoth.graph import Direction, walk
from thoth.models import Item, Relation, Status
from thoth.renames import Rename, RenameRefused, rename

router = APIRouter(tags=["items"])
EngineDep = Annotated[Engine, Depends(get_engine)]
ItemIdPath = Annotated[UUID, Path(description="The item's id.", examples=["7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11"])]


class ItemOut(BaseModel):
    """An item as it is now. `changed_*` say who made this version and why; earlier versions are its history."""

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
    openapi_extra={
        "requestBody": {
            "description": "Which item, its new key, and why.",
            "content": {"application/json": {"example": _RENAME_EXAMPLE}},
        }
    },
    responses={200: {"content": {"application/json": {"example": _ITEM_EXAMPLE}}}},
)
def rename_item(request: Rename, caller: CallerDep, engine: EngineDep) -> ItemOut:
    """Give an item a new key: same item, same relations; the old key stays in its history."""
    with Session(engine) as session:
        try:
            item = rename(session, request, caller)
            session.commit()
        except RenameRefused as refused:
            raise HTTPException(refused.status, refused.detail) from refused
        except IntegrityError as e:  # someone took the key in the meantime
            raise HTTPException(409, f"{request.type} {request.new_key} already exists") from e
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

    id: UUID = Field(examples=["7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11"])
    type: str = Field(examples=["app"])
    key: str = Field(examples=["temporal"])
    name: str = Field(examples=["Temporal"])
    status: Status = Field(examples=["active"])


class LinkOut(BaseModel):
    """A link: `from` depends on `to`."""

    model_config = {"serialize_by_alias": True}

    type: str = Field(examples=["uses"])
    from_: ItemRef = Field(alias="from")
    to: ItemRef


class Step(BaseModel):
    """An item the walk reached, how far from the start, and the link it arrived by."""

    item: ItemRef
    depth: int = Field(examples=[1])
    via: LinkOut


class WebOut(BaseModel):
    """The web around one item, built from one-hop links: what depends on it (`impact`) and what it needs
    (`dependencies`). Each item once per side, at its shortest distance, with the link it arrived by; links that would
    loop back are listed under `cycles` instead of followed."""

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


def _get(session: Session, item_id: UUID) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"no item {item_id}")
    return item


@router.get(
    "/items",
    operation_id="list_items",
    responses={200: {"content": {"application/json": {"example": [_ITEM_EXAMPLE]}}}},
)
def list_items(
    _: Reader,
    engine: EngineDep,
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
    rows = history.as_of(as_of) if as_of is not None else Item.__table__  # ty: ignore[unresolved-attribute]
    query = sa.select(rows)
    for column, value in (("type", type), ("key", key), ("status", status), ("source", source)):
        if value is not None:
            query = query.where(rows.c[column] == value)
    query = query.order_by(rows.c.type, rows.c.key).limit(limit).offset(offset)
    with Session(engine) as session:
        return [ItemOut.model_validate(dict(row._mapping)) for row in session.connection().execute(query)]


@router.get(
    "/items/{item_id}",
    operation_id="get_item",
    responses={200: {"content": {"application/json": {"example": _ITEM_EXAMPLE}}}},
)
def get_item(item_id: ItemIdPath, _: Reader, engine: EngineDep) -> ItemOut:
    """One item as it is now."""
    with Session(engine) as session:
        return ItemOut.model_validate(_get(session, item_id), from_attributes=True)


class Side(StrEnum):
    """Which side of the web to walk."""

    BOTH = "both"
    IMPACT = "impact"
    DEPENDENCIES = "dependencies"


DepthQuery = Annotated[int, Query(ge=1, le=50, description="How many links away to go; 1 = direct neighbours only.")]
SideQuery = Annotated[Side, Query(description="Both sides, or only one.")]


@router.get("/items/{item_id}/web", operation_id="get_web", responses=_WEB_RESPONSE)
def get_web(
    item_id: ItemIdPath, _: Reader, engine: EngineDep, depth: DepthQuery = 10, direction: SideQuery = Side.BOTH
) -> WebOut:
    """What depends on this item (what breaks if it goes) and what it needs to work."""
    with Session(engine) as session:
        root = _get(session, item_id)
        sides = {d: walk(session, item_id, d, depth) for d in Direction if direction in (Side.BOTH, d.value)}
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
    "/items/{item_id}/history",
    operation_id="get_item_history",
    responses={200: {"content": {"application/json": {"example": _HISTORY_EXAMPLE}}}},
)
def get_item_history(item_id: ItemIdPath, _: Reader, engine: EngineDep) -> list[VersionOut]:
    """Every version of the item, newest first, each with who made it and why."""
    with Session(engine) as session:
        _get(session, item_id)
        return [VersionOut.model_validate(v) for v in history.versions(session, item_id)]
