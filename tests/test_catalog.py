import copy
import re
from typing import Any

import pytest

from thoth.catalog import ANY_TYPE, ITEMS, RELATIONS, Source, export, owned_by
from thoth.catalog.base import ItemSpec
from thoth.catalog.compat import breaking_changes
from thoth.catalog.items import Route


def test_relation_ends_are_known_item_types() -> None:
    for spec in RELATIONS.values():
        assert spec.from_types | spec.to_types <= {*ITEMS, ANY_TYPE}, spec.type_name


def test_a_field_without_an_owner_is_refused() -> None:
    with pytest.raises(TypeError, match="has no owner"):

        class Thing(ItemSpec):
            """A thing."""

            type_name = "thing"
            colour: str


def test_a_type_without_a_docstring_is_refused() -> None:
    with pytest.raises(TypeError, match="docstring"):

        class Thing(ItemSpec):
            type_name = "thing"
            colour: str = owned_by(Source.MANUAL)


def test_a_source_sends_only_its_slice() -> None:
    assert set(Route.slice_for("caddy-scan").model_fields) == {"auth", "tls"}
    assert set(Route.slice_for("shikigami").model_fields) == {"auth"}
    with pytest.raises(ValueError, match="Extra inputs"):
        Route.slice_for("shikigami").model_validate({"tls": True})


def test_first_owner_wins() -> None:
    caddy = {"auth": "none", "tls": True}
    assert Route.merge({"caddy-scan": caddy, "shikigami": {"auth": "authentik"}}) == {"auth": "authentik", "tls": True}
    assert Route.merge({"caddy-scan": caddy}) == caddy  # nothing declared: the observed value stands


KEYS = {
    "person": (["mario"], ["Mario", "mario leal"]),
    "bot": (["thoth-r7000"], ["svc+thoth-r7000@clockmost.com"]),
    "group": (["authentik/clockmost-admins", "linux/clockmost/wheel"], ["clockmost-admins", "ldap/x"]),
    "account": (["authentik/mario", "linux/clockmost/claude-admin", "gitea/MarioGLealP"], ["mario", "linux/mario"]),
    "host": (["clockmost", "clockmost-desktop", "haos"], ["Clockmost", "clockmost.com"]),
    "device": (["a4:5e:60:12:34:56"], ["A4:5E:60:12:34:56", "a45e60123456"]),
    "network": (["r7000/br0", "k8s/clockmost"], ["br0"]),
    "cluster": (["clockmost"], ["Clockmost"]),
    "app": (["echo"], ["Echo", "echo/x"]),
    "workload": (["clockmost/echo/deployment/echo"], ["echo/deployment/echo", "clockmost/echo/pod/echo"]),
    "image": ([f"zot.clockmost.com/meru/echo@sha256:{'a' * 64}"], ["zot.clockmost.com/meru/echo:latest"]),
    "volume": (["k8s/clockmost/sindri/cache", "libvirt/clockmost/haos.qcow2"], ["/home/k8s/volumes/x"]),
    "route": (["echo.clockmost.com"], ["https://echo.clockmost.com", "Echo.clockmost.com"]),
    "database": (["clockmost/temporal/temporal-db/temporal_visibility"], ["temporal-db"]),
    "repo": (["git.clockmost.com/Meru/thoth"], ["https://git.clockmost.com/Meru/thoth", "github.com/a/b"]),
    "secret-set": (["infisical/meru/prod", "infisical/meru/prod/thoth"], ["meru/prod"]),
    "sso-provider": (["authentik/argocd"], ["argocd"]),
}


def test_every_type_has_key_examples() -> None:
    assert set(KEYS) == set(ITEMS)


@pytest.mark.parametrize("type_name", sorted(KEYS))
def test_key_patterns(type_name: str) -> None:
    good, bad = KEYS[type_name]
    pattern = ITEMS[type_name].key_pattern
    assert [k for k in good if not re.fullmatch(pattern, k)] == []
    assert [k for k in bad if re.fullmatch(pattern, k)] == []


def changed(edit: Any) -> list[str]:
    old = export()
    new = copy.deepcopy(old)
    edit(new)
    return breaking_changes(old, new)


def host_fields(c: dict[str, Any]) -> dict[str, Any]:
    return c["items"]["host"]["schema"]["properties"]


def test_unchanged_catalog_is_not_breaking() -> None:
    assert changed(lambda c: None) == []


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda c: c["items"].pop("host"), "item type host: removed"),
        (lambda c: c["items"]["host"].update(key_pattern="^.*$"), "item type host: key pattern changed"),
        (lambda c: host_fields(c).pop("os"), "item type host: field os removed"),
        (lambda c: host_fields(c)["os"].update(anyOf=[{"type": "integer"}]), "item type host: field os type changed"),
        (lambda c: host_fields(c)["os"].update({"x-owners": ["manual"]}), "item type host: field os owners changed"),
        (lambda c: c["items"]["host"]["schema"]["required"].append("os"), "item type host: field os is now required"),
        (
            lambda c: (
                host_fields(c).update(cpu={"type": "integer"}),
                c["items"]["host"]["schema"]["required"].append("cpu"),
            ),
            "item type host: new field cpu is required",
        ),
        (
            lambda c: c["relations"]["mounts"].update(to=[]),
            "relation type mounts: no longer allows volume at the to end",
        ),
    ],
)
def test_breaking_changes_are_caught(edit: Any, expected: str) -> None:
    assert any(change.startswith(expected) for change in changed(edit)), changed(edit)


@pytest.mark.parametrize(
    "edit",
    [
        lambda c: c["items"].update(printer={"description": "", "key_pattern": "^x$", "schema": {"required": ["a"]}}),
        lambda c: host_fields(c).update(cpu={"type": "integer"}),
        lambda c: c["items"]["host"].update(description="reworded"),
        lambda c: host_fields(c)["os"].update(description="reworded"),
        lambda c: c["relations"]["mounts"]["to"].append("host"),
    ],
)
def test_safe_changes_pass(edit: Any) -> None:
    assert changed(edit) == []
