"""Database access: the engine, and the one way to write - as an actor, with a reason.

Every write transaction must say who is writing and why. The history trigger (thoth.db.history) reads both from
transaction-local settings and refuses the write without them, so a change can't land unexplained.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.config import get_settings


def make_engine(url: str | None = None) -> AsyncEngine:
    """An engine on `url`, by default the configured database."""
    return create_async_engine(url or get_settings().database_url.get_secret_value(), pool_pre_ping=True)


async def write_as(
    session: AsyncSession, actor: str, reason: str, *, verdict: bool = False, retire: bool = False
) -> None:
    """Mark the current transaction with who writes and why (both reset when it ends).

    `verdict` only for a caller with thoth.verdict (Anubis): the trigger refuses to retire anything without it. `retire`
    only for a caller with thoth.retire (Shikigami): the trigger refuses decommissioning and retained without it.
    """
    if not actor or not reason:
        raise ValueError("a write needs an actor and a reason")
    # Not session.exec: that autoflushes, writing pending changes *before* the context is set - the trigger
    # would refuse them. Call it first in the transaction, or right after a commit.
    await (await session.connection()).execute(
        text(
            "select set_config('thoth.actor', :actor, true), set_config('thoth.reason', :reason, true),"
            " set_config('thoth.verdict', :verdict, true), set_config('thoth.retire', :retire, true)"
        ),
        {"actor": actor, "reason": reason, "verdict": "on" if verdict else "off", "retire": "on" if retire else "off"},
    )
