"""The change tracking lives in the database; these prove nothing can write around it."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.conftest import run_sql
from thoth.db.session import write_as
from thoth.models import Item, ItemHistory, Relation, RelationType, Status


def now() -> datetime:
    return datetime.now(UTC)


def new_host(key: str = "clockmost") -> Item:
    return Item(type="host", key=key, name=key, source="test", first_seen=now(), last_seen=now())


async def add(session: AsyncSession, *rows: object, reason: str = "test setup") -> None:
    await write_as(session, "test", reason)
    session.add_all(rows)
    await session.commit()


async def history(session: AsyncSession, item: Item) -> list[ItemHistory]:
    return list(
        await session.exec(select(ItemHistory).where(ItemHistory.id == item.id).order_by(col(ItemHistory.valid_to)))
    )


async def test_write_without_actor_is_refused(session: AsyncSession) -> None:
    session.add(new_host())
    with pytest.raises(DBAPIError, match=r"needs thoth.actor and thoth.reason"):
        await session.commit()


async def test_write_as_needs_both() -> None:
    with pytest.raises(ValueError, match="actor and a reason"):
        await write_as(AsyncSession(), "shikigami", "")  # refused before it touches a session


async def test_insert_is_stamped(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host, reason="first scan")
    await session.refresh(host)
    assert (host.changed_by, host.change_reason) == ("test", "first scan")
    assert host.changed_at is not None
    assert await history(session, host) == []  # nothing came before the first version


async def test_last_seen_bump_is_not_a_change(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    await session.refresh(host)
    stamp = host.changed_at

    host.last_seen = now()
    await add(session, host, reason="rescan")
    await session.refresh(host)
    assert host.changed_at == stamp
    assert host.change_reason == "test setup"
    assert await history(session, host) == []


async def test_real_change_keeps_the_old_version(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    await session.refresh(host)
    first_stamp = host.changed_at

    host.name = "clockmost server"
    host.attributes = {"os": "AlmaLinux 10.2"}
    await add(session, host, reason="renamed")
    await session.refresh(host)

    [old] = await history(session, host)
    assert (old.name, old.attributes, old.change_reason) == ("clockmost", {}, "test setup")
    assert old.valid_from == first_stamp
    assert old.valid_to == host.changed_at  # the old version ends where the new one starts
    assert host.change_reason == "renamed"


async def test_delete_is_refused(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    await write_as(session, "test", "cleanup")
    await session.delete(host)
    with pytest.raises(DBAPIError, match="never deleted"):
        await session.commit()


async def test_purge_can_delete(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    host.name = "renamed"
    await add(session, host)

    await write_as(session, "charon", "yearly purge")
    await run_sql(session, "select set_config('thoth.purge', 'on', true)")
    await run_sql(session, "delete from item_history")
    await session.delete(host)
    await session.commit()
    assert await session.get(Item, host.id) is None


async def test_history_is_append_only(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    host.name = "renamed"
    await add(session, host)

    await write_as(session, "test", "rewrite the past")
    with pytest.raises(DBAPIError, match="append-only"):
        await run_sql(session, "update item_history set name = 'forged'")


async def test_type_and_key_are_unique(session: AsyncSession) -> None:
    await add(session, new_host())
    with pytest.raises(IntegrityError):
        await add(session, new_host())


async def test_unknown_status_is_refused(session: AsyncSession) -> None:
    await add(session, new_host())
    await write_as(session, "test", "bad status")
    with pytest.raises(IntegrityError):
        await run_sql(session, "update item set status = 'exploded'")


async def test_relations_are_tracked_too(session: AsyncSession) -> None:
    a, b = new_host("a"), new_host("b")
    await add(session, RelationType(name="backs-up-to", description="", from_types=["host"], to_types=["host"]), a, b)
    link = Relation(type="backs-up-to", from_item=a.id, to_item=b.id, source="test", first_seen=now(), last_seen=now())
    await add(session, link, reason="seen in restic config")
    link.status = Status.MISSING
    await add(session, link, reason="not seen in the last scan")

    old = (await run_sql(session, "select status, change_reason from relation_history")).one()
    assert tuple(old) == ("active", "seen in restic config")


async def test_relation_to_itself_is_refused(session: AsyncSession) -> None:
    host = new_host()
    await add(session, RelationType(name="backs-up-to", description="", from_types=["host"], to_types=["host"]), host)
    with pytest.raises(IntegrityError, match="relation_not_to_itself"):
        await add(
            session,
            Relation(
                type="backs-up-to", from_item=host.id, to_item=host.id, source="test", first_seen=now(), last_seen=now()
            ),
        )


async def test_only_anubis_retires(session: AsyncSession) -> None:
    host = new_host()
    await add(session, host)
    host.status = Status.RETIRED
    with pytest.raises(DBAPIError, match="only anubis retires"):
        await add(session, host, reason="looks gone to me")
    await session.rollback()
    await write_as(session, "svc+anubis", "nightly verdict: gone", verdict=True)
    await run_sql(session, "update item set status = 'retired'")
    await session.commit()
    await session.refresh(host)
    assert host.status == Status.RETIRED
