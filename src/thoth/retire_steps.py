"""Shikigami's retire pass, as Thoth sees it (D85).

An app goes decommissioning when the pass starts and retained when its last step is done - stopped, trust revoked, data
kept until Charon purges it. Only Anubis retires (D53).
A restore (D106: the request is no longer removed, so onboarding runs again) takes it back to planned; the next scan
that sees it makes it active.

A retirement is a decision, not a sighting, so it isn't a snapshot: it's this one verb, for apps only, needing
thoth.retire - and the database refuses entering or leaving decommissioning/retained without the flag that permission
sets (except Anubis' retired). A step that's already done is answered as it is (the pass retries).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from thoth.auth import RETIRE, Caller
from thoth.db import write_as
from thoth.models import Item, Status

# What each step may start from: the one before it, or itself (a retried step).
FROM: dict[Status, set[Status]] = {
    Status.DECOMMISSIONING: {Status.PLANNED, Status.ACTIVE, Status.MISSING, Status.DECOMMISSIONING},
    Status.RETAINED: {Status.DECOMMISSIONING, Status.RETAINED},
    Status.PLANNED: {Status.DECOMMISSIONING, Status.RETAINED, Status.PLANNED},  # a restore (D106)
}
# Already back: a retried restore after a scan saw the app again is answered as it is.
RESTORED = {Status.ACTIVE, Status.MISSING}


class RetireStep(BaseModel):
    """One step of an app's retirement."""

    model_config = ConfigDict(frozen=True)

    app: str = Field(examples=["kitsune"])
    status: Literal["decommissioning", "retained", "planned"] = Field(
        examples=["decommissioning"], description="planned = restored: onboarding runs again (D106)"
    )
    reason: str = Field(min_length=1, max_length=500, examples=["retire pass started: Meru/requests#13"])


class StepRefusedError(Exception):
    """A retire step turned down, with the HTTP status that says why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def retire_step(session: Session, step: RetireStep, caller: Caller) -> Item:
    """Move an app to the step's status and flush it; the caller commits."""
    if not caller.may(RETIRE):
        raise StepRefusedError(403, f"needs {RETIRE}")
    item = session.exec(select(Item).where(Item.type == "app", Item.key == step.app)).first()
    if item is None:
        raise StepRefusedError(404, f"no app {step.app}")
    wanted = Status(step.status)
    if item.status == wanted or (wanted == Status.PLANNED and item.status in RESTORED):
        return item
    if item.status not in FROM[wanted]:
        raise StepRefusedError(409, f"app {step.app} is {item.status}: {wanted} comes after {sorted(FROM[wanted])}")

    write_as(session, caller.name, f"{wanted}: {step.reason}", retire=True)
    item.status = wanted
    session.flush()
    session.refresh(item)
    return item
