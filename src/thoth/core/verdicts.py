"""Anubis's side of Thoth (D53, D85, D117, D118): what scanners stopped seeing, and the verdicts on it.

The unseen queue: an item an active source + scope has gone without for 3 scans (or, for a type that comes and goes,
for its `missing_after`) while the item is still active or missing - a retiring app's absence is expected, so it isn't
queued. Anubis checks each one directly before a verdict.

A verdict is this one verb, needing thoth.verdict - and the database refuses `retired` without the flag that permission
sets. `missing`: gone, but a request still declares it. `retired`: gone for good, its links with it; or a retained app
whose purge is done. A verdict already given is answered as it is (Anubis retries).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, or_, select

from thoth.catalog import ITEMS
from thoth.core.auth import VERDICT, Caller
from thoth.db.session import write_as
from thoth.models import Item, ItemObservation, Relation, Status
from thoth.snapshots.common import due_for_anubis

QUEUED = (Status.ACTIVE, Status.MISSING)
# What each verdict may be given on.
FROM: dict[Status, set[Status]] = {
    Status.MISSING: {Status.ACTIVE},
    Status.RETIRED: {Status.ACTIVE, Status.MISSING, Status.RETAINED},
}


class Verdict(BaseModel):
    """Anubis's verdict on one item."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(examples=["workload"])
    key: str = Field(examples=["clockmost/charon/deployment/charon"])
    status: Literal["missing", "retired"] = Field(
        examples=["retired"], description="missing = gone but still declared; retired = gone for good"
    )
    reason: str = Field(min_length=1, max_length=500, examples=["gone: get deployment charon/charon answered 404"])


class VerdictRefusedError(Exception):
    """A verdict turned down, with the HTTP status that says why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def unseen(session: Session, now: datetime) -> list[tuple[Item, ItemObservation]]:
    """Every (item, observation) on Anubis's list, ordered by type, key, source, scope."""
    rows = session.exec(
        select(Item, ItemObservation)
        .join(ItemObservation, col(ItemObservation.item_id) == col(Item.id))
        .where(col(Item.status).in_(QUEUED))
        .order_by(col(Item.type), col(Item.key), col(ItemObservation.source), col(ItemObservation.scope))
    ).all()
    return [(item, obs) for item, obs in rows if obs.missed_scans > 0 and due_for_anubis(ITEMS[item.type], obs, now)]


def give(session: Session, verdict: Verdict, caller: Caller) -> Item:
    """Give the verdict and flush it; the caller commits. Retiring an item retires its links too."""
    if not caller.may(VERDICT):
        raise VerdictRefusedError(403, f"needs {VERDICT}")
    item = session.exec(select(Item).where(Item.type == verdict.type, Item.key == verdict.key)).first()
    if item is None:
        raise VerdictRefusedError(404, f"no {verdict.type} {verdict.key}")
    wanted = Status(verdict.status)
    if item.status == wanted:
        return item
    if item.status not in FROM[wanted]:
        raise VerdictRefusedError(
            409, f"{verdict.type} {verdict.key} is {item.status}: {wanted} only from {sorted(FROM[wanted])}"
        )

    write_as(session, caller.name, f"{wanted}: {verdict.reason}", verdict=True)
    item.status = wanted
    if wanted == Status.RETIRED:
        links = session.exec(
            select(Relation).where(
                or_(col(Relation.from_item) == item.id, col(Relation.to_item) == item.id),
                col(Relation.status) != Status.RETIRED,
            )
        ).all()
        for link in links:
            link.status = Status.RETIRED
    session.flush()
    session.refresh(item)
    return item
