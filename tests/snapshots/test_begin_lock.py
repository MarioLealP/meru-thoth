from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.snapshots.common import begin


async def locked_by_another(session: AsyncSession, key: str) -> bool:
    """True when another transaction holds the advisory lock for `key`."""
    got = (await session.connection()).scalar(select(func.pg_try_advisory_xact_lock(func.hashtext(key))))
    return not await got


async def test_begin_holds_the_lock_for_its_source_until_the_transaction_ends(db: AsyncEngine) -> None:
    async with AsyncSession(db) as first, AsyncSession(db) as other:
        await begin(first, "host-scan", "host-scan", "clockmost-desktop", "items")
        assert await locked_by_another(other, "items:host-scan@clockmost-desktop")
        await other.rollback()
        assert not await locked_by_another(other, "items:host-scan@elsewhere")
        await first.rollback()
        await other.rollback()
        assert not await locked_by_another(other, "items:host-scan@clockmost-desktop")
