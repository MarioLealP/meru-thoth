"""What item and relation snapshots share: the result, refusal, slices, misses and the lifecycle rules."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlmodel import Session

from thoth.catalog.base import Spec
from thoth.db import write_as
from thoth.models import Status

SCAN_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
MISSES_FOR_ANUBIS = 3


class Summary(BaseModel):
    """What a snapshot changed."""

    model_config = ConfigDict(frozen=True)

    created: int = Field(examples=[1])
    changed: int = Field(examples=[2])
    unchanged: int = Field(examples=[40])
    missed: int = Field(examples=[1])  # reported before, not in this scan
    for_anubis: list[str] = Field(examples=[["account linux/clockmost-desktop/olduser"]])
    skipped: list[str] = Field(default_factory=list, examples=[[]])  # links whose other end doesn't exist yet


class SnapshotRefusedError(Exception):
    """A snapshot turned down whole, with every reason."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"snapshot refused: {len(errors)} problems")
        self.errors = errors


class Observation(Protocol):
    """What the snapshot rules use of an item or relation observation."""

    source: str
    attributes: dict[str, Any]
    last_seen: datetime
    missed_scans: int
    last_scan: str


class Tracked(Protocol):
    """A row with a lifecycle: an item or a relation."""

    status: Status


def current_slices(observations: Iterable[Observation]) -> dict[str, dict[str, Any]]:
    """Each source's slice; a source seen from several scopes counts its freshest one."""
    slices: dict[str, dict[str, Any]] = {}
    for obs in sorted(observations, key=lambda o: (o.missed_scans == 0, o.last_seen)):
        slices[obs.source] = obs.attributes
    return slices


def begin(session: Session, actor: str, source: str, scope: str, kind: str) -> None:
    """Who and why for the trigger; one snapshot per source + scope + kind at a time."""
    write_as(session, actor, f"snapshot from {source}@{scope}")
    session.connection().execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{kind}:{source}@{scope}"})


def observed(obs: Observation, attributes: dict[str, Any], now: datetime, scan_id: str) -> None:
    """Record that this scan saw the thing, with these attributes."""
    obs.attributes, obs.last_seen, obs.missed_scans, obs.last_scan = attributes, now, 0, scan_id


def missed(obs: Observation, scan_id: str) -> bool:
    """Count a miss - once per scan, so a resent snapshot doesn't count it twice."""
    if obs.last_scan == scan_id:
        return False
    obs.missed_scans += 1
    obs.last_scan = scan_id
    return True


def due_for_anubis(spec: type[Spec], obs: Observation, now: datetime) -> bool:
    """Whether something unseen has been gone long enough for Anubis's list."""
    if spec.missing_after is not None:  # comes and goes: time unseen, not scans without it
        return now - obs.last_seen >= spec.missing_after
    return obs.missed_scans >= MISSES_FOR_ANUBIS


def seen(row: Tracked, declaring: bool) -> None:
    """Being seen makes planned or missing things active; decommissioning/retired wait for Anubis (D53)."""
    if not declaring and row.status in (Status.PLANNED, Status.MISSING):
        row.status = Status.ACTIVE
