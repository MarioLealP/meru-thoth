"""Reading the past (D39): an item's versions, and the inventory as it was at a moment.

The current row is valid from its `changed_at`; each history row from `valid_from` to `valid_to`. So the version of
an item at time T is the current row if it changed at or before T, else the history row whose window holds T.
Items whose first version starts after T didn't exist yet.
"""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.models import Item, ItemHistory, table_of

_COLUMNS = ("id", "type", "key", "name", "status", "attributes", "source", "first_seen", "last_seen", "changed_at",
            "changed_by", "change_reason")  # fmt: skip


async def versions(session: AsyncSession, current: Item) -> list[dict[str, Any]]:
    """Newest first; the current version has valid_to None."""
    rows = [{**current.model_dump(include=set(_COLUMNS)), "valid_from": current.changed_at, "valid_to": None}]
    older = select(ItemHistory).where(ItemHistory.id == current.id).order_by(col(ItemHistory.valid_to).desc())
    rows += [
        {**h.model_dump(include=set(_COLUMNS)), "valid_from": h.valid_from, "valid_to": h.valid_to}
        for h in (await session.exec(older))
    ]
    return rows


def as_of(at: datetime) -> sa.Subquery:
    """Every item as it was at `at`, with the item table's columns - filter and page it like the item table."""
    item, hist = table_of(Item), table_of(ItemHistory)
    now_rows = sa.select(*(item.c[c] for c in _COLUMNS)).where(item.c.changed_at <= at)
    old_rows = sa.select(*(hist.c[c] for c in _COLUMNS)).where(hist.c.valid_from <= at, hist.c.valid_to > at)
    return sa.union_all(now_rows, old_rows).subquery("item_as_of")
