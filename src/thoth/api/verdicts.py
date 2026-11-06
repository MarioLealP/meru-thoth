"""Routes for Anubis (D53, D118): the unseen queue it works through, and its verdicts."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from thoth.api.deps import CallerDep, EngineDep, Reader
from thoth.api.items import ItemOut
from thoth.core.verdicts import Verdict, VerdictRefusedError, give, unseen

router = APIRouter(tags=["verdicts"])


class UnseenOut(BaseModel):
    """An item a source + scope stopped seeing long enough for Anubis to check it."""

    model_config = ConfigDict(frozen=True)

    item: ItemOut
    source: str = Field(examples=["k8s-scan"])
    scope: str = Field(examples=["clockmost"])
    missed_scans: int = Field(examples=[3])
    last_seen: datetime = Field(examples=["2026-11-04T23:40:00Z"])


_ITEM = {
    "id": "7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11",
    "type": "workload",
    "key": "clockmost/charon/deployment/charon",
    "name": "charon",
    "status": "active",
    "attributes": {},
    "source": "k8s-scan",
    "first_seen": "2026-11-03T15:39:00Z",
    "last_seen": "2026-11-03T15:59:00Z",
    "changed_at": "2026-11-03T15:39:00Z",
    "changed_by": "heimdall",
    "change_reason": "snapshot from k8s-scan@clockmost",
}
_VERDICT_EXAMPLE = {
    "type": "workload",
    "key": "clockmost/charon/deployment/charon",
    "status": "retired",
    "reason": "gone: get deployment charon/charon answered 404",
}


@router.get(
    "/unseen",
    operation_id="list_unseen",
    status_code=200,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": [
                        {
                            "item": _ITEM,
                            "source": "k8s-scan",
                            "scope": "clockmost",
                            "missed_scans": 3,
                            "last_seen": "2026-11-03T15:59:00Z",
                        }
                    ]
                }
            }
        }
    },
)
def list_unseen(_: Reader, engine: EngineDep) -> list[UnseenOut]:
    """What scanners stopped seeing: 3 scans in a row, or a coming-and-going type's `missing_after` (D53)."""
    with Session(engine) as session:
        return [
            UnseenOut(
                item=ItemOut.model_validate(item, from_attributes=True),
                source=obs.source,
                scope=obs.scope,
                missed_scans=obs.missed_scans,
                last_seen=obs.last_seen,
            )
            for item, obs in unseen(session, datetime.now(UTC))
        ]


@router.post(
    "/verdicts",
    operation_id="give_verdict",
    status_code=200,
    openapi_extra={
        "requestBody": {
            "description": "Which item, the verdict, and why.",
            "content": {"application/json": {"example": _VERDICT_EXAMPLE}},
        }
    },
    responses={200: {"content": {"application/json": {"example": {**_ITEM, "status": "retired"}}}}},
)
def post_verdict(request: Verdict, caller: CallerDep, engine: EngineDep) -> ItemOut:
    """Anubis's verdict (D53): missing or retired - retiring takes the item's links with it. Needs thoth.verdict.

    A verdict already given is answered as it is.
    """
    with Session(engine) as session:
        try:
            item = give(session, request, caller)
            session.commit()
        except VerdictRefusedError as refused:
            raise HTTPException(refused.status, refused.detail) from refused
        return ItemOut.model_validate(item, from_attributes=True)
