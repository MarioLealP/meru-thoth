"""Routes for scans: what sources report, and the log of their snapshots."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import distinct_on
from sqlmodel import Session, col, select

from thoth import snapshots
from thoth.api.deps import CallerDep, EngineDep, Reader
from thoth.api.problems import Problem, ProblemError
from thoth.auth import Caller, report
from thoth.catalog import Source
from thoth.catalog.scopes import SCOPE_PATTERN
from thoth.models import SnapshotRun
from thoth.snapshots import SCAN_ID_PATTERN, ReportedItem, ReportedRelation, Summary

router = APIRouter(tags=["scans"])

ScanPath = Annotated[
    str, Path(description="The scan run this belongs to (the workflow's id).", pattern=SCAN_ID_PATTERN,
              examples=["scan-2026-10-29T12-00"])
]  # fmt: skip
SourcePath = Annotated[Source, Path(description="Who is reporting.", examples=["host-scan"])]
ScopePath = Annotated[
    str,
    Path(
        description="Where it looked: a machine or cluster name, or `all` for sources that see a whole system.",
        pattern=SCOPE_PATTERN,
        max_length=63,
        examples=["clockmost-desktop"],
    ),
]

_DESKTOP = {"type": "host", "key": "clockmost-desktop"}
_BENYLIN = {"type": "account", "key": "linux/clockmost-desktop/benylin"}
_ITEMS_EXAMPLE: dict[str, Any] = {
    "items": [
        {**_DESKTOP, "name": "clockmost-desktop", "attributes": {"kind": "physical", "os": "CachyOS"}},
        {**_BENYLIN, "name": "benylin", "attributes": {"email": "lealmariop@gmail.com"}},
    ]
}
_RELATIONS_EXAMPLE: dict[str, Any] = {
    "relations": [
        {"type": "member-of", "from": _BENYLIN, "to": {"type": "group", "key": "linux/clockmost-desktop/wheel"},
         "attributes": {"role": None}},
    ]
}  # fmt: skip
_RUN_EXAMPLE = {
    "scan_id": "scan-2026-10-29T12-00",
    "kind": "items",
    "source": "host-scan",
    "scope": "clockmost-desktop",
    "at": "2026-10-29T12:00:00Z",
    "accepted": True,
    "summary": {"created": 0, "changed": 1, "unchanged": 40, "missed": 0, "for_anubis": [], "skipped": []},
}


class ItemSnapshot(BaseModel):
    """Every item a source sees from one scope - its whole picture, not a diff."""

    model_config = ConfigDict(frozen=True)

    items: list[ReportedItem] = Field(max_length=20_000, examples=[_ITEMS_EXAMPLE["items"]])


class RelationSnapshot(BaseModel):
    """Every link a source sees from one scope."""

    model_config = ConfigDict(frozen=True)

    relations: list[ReportedRelation] = Field(max_length=50_000, examples=[_RELATIONS_EXAMPLE["relations"]])


class SnapshotRunOut(BaseModel):
    """A source's latest snapshot: when, and whether it was accepted (with counts) or refused (with reasons)."""

    model_config = ConfigDict(frozen=True)

    scan_id: str = Field(examples=["scan-2026-10-29T12-00"])
    kind: str = Field(examples=["items"])
    source: str = Field(examples=["host-scan"])
    scope: str = Field(examples=["clockmost-desktop"])
    at: datetime
    accepted: bool
    summary: dict = Field(examples=[_RUN_EXAMPLE["summary"]])


def _may_report(caller: Caller, source: str, scope: str) -> None:
    """The token must carry exactly this source + scope (D52, D56) - where a scan came from is the login's."""
    if not caller.may(needed := report(source, scope)):
        raise HTTPException(403, f"needs {needed}")


def _body(description: str, example: dict[str, Any]) -> dict[str, Any]:
    return {"requestBody": {"description": description, "content": {"application/json": {"example": example}}}}


def _record(engine: Engine, run: dict[str, Any], apply: Any) -> Summary:
    """Apply a snapshot and log the run; a refusal is logged too, after rolling back, then answered as a problem."""
    with Session(engine) as session:
        try:
            summary = apply(session)
        except snapshots.SnapshotRefusedError as refused:
            session.rollback()
            errors = refused.errors
            session.add(SnapshotRun(**run, accepted=False, summary={"errors": errors[:100]}))
            session.commit()
            raise ProblemError(
                Problem(
                    title="Snapshot refused",
                    status=422,
                    detail=f"{len(errors)} problems - nothing was applied",
                    errors=[{"msg": e} for e in errors[:100]],
                )
            ) from refused
        session.add(SnapshotRun(**run, accepted=True, summary=summary.model_dump()))
        session.commit()
    return summary


@router.put(
    "/scans/{scan_id}/{source}/{scope}/items",
    operation_id="put_scan_items",
    status_code=200,
    openapi_extra=_body("Every item the source sees from this scope.", _ITEMS_EXAMPLE),
)
def put_scan_items(
    scan_id: ScanPath,
    source: SourcePath,
    scope: ScopePath,
    snapshot: ItemSnapshot,
    caller: CallerDep,
    engine: EngineDep,
) -> Summary:
    """Phase 1 of a scan: what `source` sees from `scope`.

    All or nothing - anything wrong refuses the whole snapshot and keeps the last good data (the refusal is logged, for
    Zabbix). Sending the same scan again changes nothing.
    """
    _may_report(caller, source, scope)
    now = datetime.now(UTC)
    run = {"scan_id": scan_id, "kind": "items", "source": source, "scope": scope, "at": now}
    return _record(
        engine, run, lambda s: snapshots.apply_items(s, caller.name, scan_id, source, scope, snapshot.items, now)
    )


@router.put(
    "/scans/{scan_id}/{source}/{scope}/relations",
    operation_id="put_scan_relations",
    status_code=200,
    openapi_extra=_body("Every link the source sees from this scope.", _RELATIONS_EXAMPLE),
)
def put_scan_relations(
    scan_id: ScanPath,
    source: SourcePath,
    scope: ScopePath,
    snapshot: RelationSnapshot,
    caller: CallerDep,
    engine: EngineDep,
) -> Summary:
    """Phase 2 of a scan, once every source's items are in.

    A link whose other end doesn't exist yet is skipped and listed (it heals on the next scan); anything else wrong
    refuses the whole snapshot.
    """
    _may_report(caller, source, scope)
    now = datetime.now(UTC)
    run = {"scan_id": scan_id, "kind": "relations", "source": source, "scope": scope, "at": now}
    return _record(
        engine,
        run,
        lambda s: snapshots.apply_relations(s, caller.name, scan_id, source, scope, snapshot.relations, now),
    )


@router.get(
    "/snapshots",
    operation_id="list_snapshots",
    status_code=200,
    responses={200: {"content": {"application/json": {"example": [_RUN_EXAMPLE]}}}},
)
def list_snapshots(_: Reader, engine: EngineDep) -> list[SnapshotRunOut]:
    """The latest snapshot of every source + scope + kind - Zabbix watches this for refused or quiet sources."""
    with Session(engine) as session:
        latest = (
            select(SnapshotRun)
            .ext(distinct_on(col(SnapshotRun.source), col(SnapshotRun.scope), col(SnapshotRun.kind)))
            .order_by(
                col(SnapshotRun.source), col(SnapshotRun.scope), col(SnapshotRun.kind), col(SnapshotRun.at).desc()
            )
        )
        return [SnapshotRunOut.model_validate(run, from_attributes=True) for run in session.exec(latest)]
