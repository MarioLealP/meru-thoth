"""The change tracking lives in the database; these prove nothing can write around it."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, col, select

from tests.conftest import run_sql
from thoth.db import write_as
from thoth.models import Item, ItemHistory, Relation, RelationType, Status


def now() -> datetime:
    return datetime.now(UTC)


def new_host(key: str = "clockmost") -> Item:
    return Item(type="host", key=key, name=key, source="test", first_seen=now(), last_seen=now())


def add(session: Session, *rows: object, reason: str = "test setup") -> None:
    write_as(session, "test", reason)
    session.add_all(rows)
    session.commit()


def history(session: Session, item: Item) -> list[ItemHistory]:
    return list(session.exec(select(ItemHistory).where(ItemHistory.id == item.id).order_by(col(ItemHistory.valid_to))))


def test_write_without_actor_is_refused(session: Session) -> None:
    session.add(new_host())
    with pytest.raises(DBAPIError, match=r"needs thoth.actor and thoth.reason"):
        session.commit()


def test_write_as_needs_both() -> None:
    with pytest.raises(ValueError, match="actor and a reason"):
        write_as(Session(), "shikigami", "")  # refused before it touches a session


def test_insert_is_stamped(session: Session) -> None:
    host = new_host()
    add(session, host, reason="first scan")
    session.refresh(host)
    assert (host.changed_by, host.change_reason) == ("test", "first scan")
    assert host.changed_at is not None
    assert history(session, host) == []  # nothing came before the first version


def test_last_seen_bump_is_not_a_change(session: Session) -> None:
    host = new_host()
    add(session, host)
    session.refresh(host)
    stamp = host.changed_at

    host.last_seen = now()
    add(session, host, reason="rescan")
    session.refresh(host)
    assert host.changed_at == stamp
    assert host.change_reason == "test setup"
    assert history(session, host) == []


def test_real_change_keeps_the_old_version(session: Session) -> None:
    host = new_host()
    add(session, host)
    session.refresh(host)
    first_stamp = host.changed_at

    host.name = "clockmost server"
    host.attributes = {"os": "AlmaLinux 10.2"}
    add(session, host, reason="renamed")
    session.refresh(host)

    [old] = history(session, host)
    assert (old.name, old.attributes, old.change_reason) == ("clockmost", {}, "test setup")
    assert old.valid_from == first_stamp
    assert old.valid_to == host.changed_at  # the old version ends where the new one starts
    assert host.change_reason == "renamed"


def test_delete_is_refused(session: Session) -> None:
    host = new_host()
    add(session, host)
    write_as(session, "test", "cleanup")
    session.delete(host)
    with pytest.raises(DBAPIError, match="never deleted"):
        session.commit()


def test_purge_can_delete(session: Session) -> None:
    host = new_host()
    add(session, host)
    host.name = "renamed"
    add(session, host)

    write_as(session, "charon", "yearly purge")
    run_sql(session, "select set_config('thoth.purge', 'on', true)")
    run_sql(session, "delete from item_history")
    session.delete(host)
    session.commit()
    assert session.get(Item, host.id) is None


def test_history_is_append_only(session: Session) -> None:
    host = new_host()
    add(session, host)
    host.name = "renamed"
    add(session, host)

    write_as(session, "test", "rewrite the past")
    with pytest.raises(DBAPIError, match="append-only"):
        run_sql(session, "update item_history set name = 'forged'")


def test_type_and_key_are_unique(session: Session) -> None:
    add(session, new_host())
    with pytest.raises(IntegrityError):
        add(session, new_host())


def test_unknown_status_is_refused(session: Session) -> None:
    add(session, new_host())
    write_as(session, "test", "bad status")
    with pytest.raises(IntegrityError):
        run_sql(session, "update item set status = 'exploded'")


def test_relations_are_tracked_too(session: Session) -> None:
    a, b = new_host("a"), new_host("b")
    add(session, RelationType(name="backs-up-to", description="", from_types=["host"], to_types=["host"]), a, b)
    link = Relation(type="backs-up-to", from_item=a.id, to_item=b.id, source="test", first_seen=now(), last_seen=now())
    add(session, link, reason="seen in restic config")
    link.status = Status.MISSING
    add(session, link, reason="not seen in the last scan")

    old = run_sql(session, "select status, change_reason from relation_history").one()
    assert tuple(old) == ("active", "seen in restic config")


def test_relation_to_itself_is_refused(session: Session) -> None:
    host = new_host()
    add(session, RelationType(name="backs-up-to", description="", from_types=["host"], to_types=["host"]), host)
    with pytest.raises(IntegrityError, match="relation_not_to_itself"):
        add(
            session,
            Relation(
                type="backs-up-to", from_item=host.id, to_item=host.id, source="test", first_seen=now(), last_seen=now()
            ),
        )


def test_only_anubis_retires(session: Session) -> None:
    host = new_host()
    add(session, host)
    host.status = Status.RETIRED
    with pytest.raises(DBAPIError, match="only anubis retires"):
        add(session, host, reason="looks gone to me")
    session.rollback()
    write_as(session, "svc+anubis", "nightly verdict: gone", verdict=True)
    run_sql(session, "update item set status = 'retired'")
    session.commit()
    session.refresh(host)
    assert host.status == Status.RETIRED
