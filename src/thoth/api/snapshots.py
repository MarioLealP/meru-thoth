from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import distinct_on
from sqlmodel import Session, col, select

from thoth import snapshots
from thoth.api.deps import get_engine
from thoth.api.problems import Problem, ProblemError
from thoth.catalog import Source
from thoth.catalog.scopes import SCOPE_PATTERN
from thoth.models import SnapshotRun
from thoth.snapshots import ReportedItem, Summary

router = APIRouter(tags=["snapshots"])

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
_SNAPSHOT_EXAMPLE: dict[str, Any] = {
    "items": [
        {"type": "host", "key": "clockmost-desktop", "name": "clockmost-desktop",
         "attributes": {"kind": "physical", "os": "CachyOS"}},
        {"type": "account", "key": "linux/clockmost-desktop/benylin", "name": "benylin",
         "attributes": {"email": "lealmariop@gmail.com"}},
    ]
}  # fmt: skip
_RUN_EXAMPLE = {
    "source": "host-scan",
    "scope": "clockmost-desktop",
    "at": "2026-10-29T12:00:00Z",
    "accepted": True,
    "summary": {"created": 0, "changed": 1, "unchanged": 40, "missed": 0, "for_anubis": []},
}


class Snapshot(BaseModel):
    """Everything a source sees from one scope - its whole picture, not a diff."""

    items: list[ReportedItem] = Field(max_length=20_000, examples=[_SNAPSHOT_EXAMPLE["items"]])


class SnapshotRunOut(BaseModel):
    """A source's latest snapshot: when, and whether it was accepted (with counts) or refused (with reasons)."""

    source: str = Field(examples=["host-scan"])
    scope: str = Field(examples=["clockmost-desktop"])
    at: datetime
    accepted: bool
    summary: dict = Field(examples=[{"created": 0, "changed": 1, "unchanged": 40, "missed": 0, "for_anubis": []}])


@router.put(
    "/snapshots/{source}/{scope}",
    operation_id="put_snapshot",
    openapi_extra={
        "requestBody": {
            "description": "The source's whole picture from this scope.",
            "content": {"application/json": {"example": _SNAPSHOT_EXAMPLE}},
        }
    },
)
def put_snapshot(
    source: SourcePath,
    scope: ScopePath,
    snapshot: Snapshot,
    engine: Annotated[Engine, Depends(get_engine)],
) -> Summary:
    """Report what `source` sees from `scope`. All or nothing: anything wrong refuses the whole snapshot and keeps
    the last good data (every refusal is logged, for Zabbix). Things missing 3 snapshots in a row go to Anubis."""
    now = datetime.now(UTC)
    with Session(engine) as session:
        try:
            summary = snapshots.apply(session, source, scope, snapshot.items, now)
        except snapshots.SnapshotRefused as refused:
            session.rollback()
            errors = refused.errors
            session.add(
                SnapshotRun(source=source, scope=scope, at=now, accepted=False, summary={"errors": errors[:100]})
            )
            session.commit()
            raise ProblemError(
                Problem(
                    title="Snapshot refused",
                    status=422,
                    detail=f"{len(errors)} problems - nothing was applied",
                    errors=[{"msg": e} for e in errors[:100]],
                )
            ) from refused
        session.add(SnapshotRun(source=source, scope=scope, at=now, accepted=True, summary=summary.model_dump()))
        session.commit()
    return summary


@router.get(
    "/snapshots",
    operation_id="list_snapshots",
    responses={200: {"content": {"application/json": {"example": [_RUN_EXAMPLE]}}}},
)
def list_snapshots(engine: Annotated[Engine, Depends(get_engine)]) -> list[SnapshotRunOut]:
    """The latest snapshot of every source + scope - Zabbix watches this for refused or stale sources."""
    with Session(engine) as session:
        latest = (
            select(SnapshotRun)
            .ext(distinct_on(col(SnapshotRun.source), col(SnapshotRun.scope)))
            .order_by(col(SnapshotRun.source), col(SnapshotRun.scope), col(SnapshotRun.at).desc())
        )
        return [SnapshotRunOut.model_validate(run, from_attributes=True) for run in session.exec(latest)]
