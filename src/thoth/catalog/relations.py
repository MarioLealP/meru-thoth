"""The v1 relation types (D38, D43). Direction is dependent first: follow arrows backwards for impact."""

from datetime import timedelta
from typing import ClassVar, Literal

from thoth.catalog.base import ANY_TYPE, RelationSpec, Source, owned_by


class OwnedBy(RelationSpec):
    """Who answers for it. Ownership is always this relation, never a column."""

    type_name = "owned-by"
    from_types: ClassVar[frozenset[str]] = frozenset({ANY_TYPE})
    to_types: ClassVar[frozenset[str]] = frozenset({"person", "group"})


class HasAccount(RelationSpec):
    """A person or bot uses this login (linked on an exact email match)."""

    type_name = "has-account"
    from_types: ClassVar[frozenset[str]] = frozenset({"person", "bot"})
    to_types: ClassVar[frozenset[str]] = frozenset({"account"})


class MemberOf(RelationSpec):
    """Membership: of a group, or a host's of a cluster."""

    type_name = "member-of"
    from_types: ClassVar[frozenset[str]] = frozenset({"person", "bot", "account", "group", "host"})
    to_types: ClassVar[frozenset[str]] = frozenset({"group", "cluster"})

    role: Literal["server", "agent"] | None = owned_by(Source.K8S, default=None)  # hosts in a cluster


class PartOf(RelationSpec):
    """A piece of an app."""

    type_name = "part-of"
    from_types: ClassVar[frozenset[str]] = frozenset(
        {"workload", "image", "volume", "route", "database", "repo", "secret-set", "sso-provider"}
    )
    to_types: ClassVar[frozenset[str]] = frozenset({"app"})


class Runs(RelationSpec):
    """A workload runs this image."""

    type_name = "runs"
    from_types: ClassVar[frozenset[str]] = frozenset({"workload"})
    to_types: ClassVar[frozenset[str]] = frozenset({"image"})


class Mounts(RelationSpec):
    """A workload mounts this volume."""

    type_name = "mounts"
    from_types: ClassVar[frozenset[str]] = frozenset({"workload"})
    to_types: ClassVar[frozenset[str]] = frozenset({"volume"})

    path: str = owned_by(Source.K8S)
    read_only: bool = owned_by(Source.K8S, default=False)


class Uses(RelationSpec):
    """Needs it to work: a database, secrets, a login provider, another app."""

    type_name = "uses"
    from_types: ClassVar[frozenset[str]] = frozenset({"app", "workload"})
    to_types: ClassVar[frozenset[str]] = frozenset({"app", "database", "secret-set", "sso-provider"})


class AttachedTo(RelationSpec):
    """A machine on a network segment."""

    type_name = "attached-to"
    from_types: ClassVar[frozenset[str]] = frozenset({"host", "device"})
    to_types: ClassVar[frozenset[str]] = frozenset({"network"})
    missing_after: ClassVar[timedelta | None] = timedelta(days=30)  # the desktop isn't on all day

    ip: str | None = owned_by(Source.ROUTER, Source.HOST, default=None)


class Exposes(RelationSpec):
    """A port forward: the network sends outside traffic to this machine."""

    type_name = "exposes"
    from_types: ClassVar[frozenset[str]] = frozenset({"network"})
    to_types: ClassVar[frozenset[str]] = frozenset({"host", "device"})

    port: int = owned_by(Source.ROUTER)
    protocol: Literal["tcp", "udp", "both"] = owned_by(Source.ROUTER)


class PointsTo(RelationSpec):
    """Where a route sends its traffic."""

    type_name = "points-to"
    from_types: ClassVar[frozenset[str]] = frozenset({"route"})
    to_types: ClassVar[frozenset[str]] = frozenset({"workload", "host"})

    port: int | None = owned_by(Source.SHIKIGAMI, Source.CADDY, default=None)


class StoredOn(RelationSpec):
    """The disk this volume lives on (local-path is one node's disk; a VM disk is its hypervisor's)."""

    type_name = "stored-on"
    from_types: ClassVar[frozenset[str]] = frozenset({"volume"})
    to_types: ClassVar[frozenset[str]] = frozenset({"host"})


class PinnedTo(RelationSpec):
    """A workload that can only run on this host (USB devices, GPUs)."""

    type_name = "pinned-to"
    from_types: ClassVar[frozenset[str]] = frozenset({"workload"})
    to_types: ClassVar[frozenset[str]] = frozenset({"host"})


class BuiltFrom(RelationSpec):
    """The repository an image was built from."""

    type_name = "built-from"
    from_types: ClassVar[frozenset[str]] = frozenset({"image"})
    to_types: ClassVar[frozenset[str]] = frozenset({"repo"})


class HostedOn(RelationSpec):
    """A VM runs on this hypervisor host."""

    type_name = "hosted-on"
    from_types: ClassVar[frozenset[str]] = frozenset({"host"})
    to_types: ClassVar[frozenset[str]] = frozenset({"host"})


RELATION_SPECS: tuple[type[RelationSpec], ...] = (
    OwnedBy, HasAccount, MemberOf, PartOf, Runs, Mounts, Uses,
    AttachedTo, Exposes, PointsTo, StoredOn, PinnedTo, BuiltFrom, HostedOn,
)  # fmt: skip
