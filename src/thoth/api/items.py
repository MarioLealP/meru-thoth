from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from thoth.api.deps import get_engine
from thoth.models import Status
from thoth.renames import Rename, RenameRefused, rename

router = APIRouter(tags=["items"])


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
    "by": "manual",
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
def rename_item(request: Rename, engine: Annotated[Engine, Depends(get_engine)]) -> ItemOut:
    """Give an item a new key: same item, same relations; the old key stays in its history."""
    with Session(engine) as session:
        try:
            item = rename(session, request)
            session.commit()
        except RenameRefused as refused:
            raise HTTPException(refused.status, refused.detail) from refused
        except IntegrityError as e:  # someone took the key in the meantime
            raise HTTPException(409, f"{request.type} {request.new_key} already exists") from e
        return ItemOut.model_validate(item, from_attributes=True)
