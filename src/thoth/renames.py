"""Renaming an item: same item (same id, same relations, same slices), new key - never guessed from scans (D40).

The history trigger keeps the old key and name with who renamed it and why. After a rename, scanners report the
new key and land on the same item; a scanner still reporting the old key creates a new item - something by that
name still exists.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from thoth.auth import MANUAL, Caller
from thoth.catalog import ITEMS
from thoth.db import write_as
from thoth.models import Item, Status


class Rename(BaseModel):
    """Give an item a new key (and optionally a new name)."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(examples=["host"])
    key: str = Field(examples=["nitro5"])
    new_key: str = Field(examples=["clockmost-ai"])
    new_name: str | None = Field(default=None, examples=["clockmost-ai"])
    reason: str = Field(min_length=1, examples=["the Nitro 5 became the AI node"])


class RenameRefusedError(Exception):
    """A rename turned down, with the HTTP status that says why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def rename(session: Session, request: Rename, caller: Caller) -> Item:
    """Give an item a new key and flush it; the caller commits."""
    spec = ITEMS.get(request.type)
    if spec is None:
        raise RenameRefusedError(422, f"unknown type {request.type}")
    if not spec.key_ok(request.new_key):
        raise RenameRefusedError(422, f"{request.new_key} doesn't match {spec.key_pattern}")
    item = session.exec(select(Item).where(Item.type == request.type, Item.key == request.key)).first()
    if item is None:
        raise RenameRefusedError(404, f"no {request.type} {request.key}")
    if not (caller.may(MANUAL) or caller.reports_as(item.source)):
        raise RenameRefusedError(403, f"needs {MANUAL}, or to report as {item.source} (which reported it first)")
    if item.status == Status.RETIRED:
        raise RenameRefusedError(409, f"{request.type} {request.key} is retired")
    if session.exec(select(Item).where(Item.type == request.type, Item.key == request.new_key)).first():
        raise RenameRefusedError(409, f"{request.type} {request.new_key} already exists")

    write_as(session, caller.name, f"rename {request.key} -> {request.new_key}: {request.reason}")
    item.key = request.new_key
    if request.new_name is not None:
        item.name = request.new_name
    session.flush()
    session.refresh(item)
    return item
