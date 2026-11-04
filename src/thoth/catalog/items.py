"""The v1 item types (D36, D40, D41, D43, D45). Attributes start small: each source adds its fields when it's built."""

from datetime import timedelta
from typing import ClassVar, Literal

from thoth.catalog.base import ItemSpec, Source, owned_by

_NAME = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"  # a DNS label: hosts, clusters, namespaces, apps
_LOGIN = r"[A-Za-z0-9._+@-]+"  # the last part of an account or group key, as its system spells it
_SYSTEM = rf"(authentik|gitea|zot|linux/{_NAME}|k8s/{_NAME}/{_NAME})"  # where an account or group lives


# People and identities


class Person(ItemSpec):
    """A human. Accounts link to a person only on an exact email match; names are for display and search."""

    type_name = "person"
    key_pattern: ClassVar[str] = rf"^{_NAME}$"

    names: list[str] = owned_by(Source.MANUAL, default_factory=list)
    emails: list[str] = owned_by(Source.MANUAL, default_factory=list)


class Bot(ItemSpec):
    """A non-human identity, one per job, owned by a person. Its accounts link to it by its email."""

    type_name = "bot"
    key_pattern: ClassVar[str] = rf"^{_NAME}$"

    email: str = owned_by(Source.SHIKIGAMI, Source.MANUAL, pattern=rf"^svc\+{_NAME}@clockmost\.com$")
    purpose: str = owned_by(Source.SHIKIGAMI, Source.MANUAL)


class Group(ItemSpec):
    """A group on one system, keyed `<system>/<name>`: authentik/clockmost-admins, linux/clockmost/wheel."""

    type_name = "group"
    key_pattern: ClassVar[str] = rf"^{_SYSTEM}/{_LOGIN}$"


class Account(ItemSpec):
    """A login on one system, keyed `<system>/<login>`: authentik/mario, linux/clockmost/benylin, gitea/MarioGLealP.

    Every account should resolve to a person or a bot - one that doesn't is unexplained.
    """

    type_name = "account"
    key_pattern: ClassVar[str] = rf"^{_SYSTEM}/{_LOGIN}$"

    # Only one system ever reports a given account, so the order here never decides anything.
    email: str | None = owned_by(Source.AUTHENTIK, Source.GITEA, Source.HOST, default=None)


# Machines and networks


class Host(ItemSpec):
    """An operating system instance - a physical machine or a VM - keyed by its short hostname."""

    type_name = "host"
    key_pattern: ClassVar[str] = rf"^{_NAME}$"

    kind: Literal["physical", "vm"] = owned_by(Source.HOST)
    os: str | None = owned_by(Source.HOST, default=None)


class Device(ItemSpec):
    """Anything on the network that isn't a host we manage (phones, printers, IoT), keyed by MAC address."""

    type_name = "device"
    key_pattern: ClassVar[str] = r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$"
    missing_after: ClassVar[timedelta | None] = timedelta(days=30)  # phones leave, things get unplugged

    label: str | None = owned_by(Source.MANUAL, default=None)


class Network(ItemSpec):
    """A network segment, keyed `<where>/<name>`: r7000/br0 (the LAN), r7000/br1 (IoT), k8s/clockmost."""

    type_name = "network"
    key_pattern: ClassVar[str] = rf"^{_NAME}/[a-z0-9.-]+$"

    kind: Literal["lan", "vlan", "k8s"] = owned_by(Source.ROUTER, Source.K8S)
    cidr: str | None = owned_by(Source.ROUTER, Source.K8S, default=None)


class Cluster(ItemSpec):
    """A Kubernetes cluster. Hosts join it with member-of (role server or agent)."""

    type_name = "cluster"
    key_pattern: ClassVar[str] = rf"^{_NAME}$"

    version: str | None = owned_by(Source.K8S, default=None)


# Meru apps and their parts


class App(ItemSpec):
    """A Meru app: the unit someone requests, owns and retires. Its parts point at it with part-of."""

    type_name = "app"
    key_pattern: ClassVar[str] = rf"^{_NAME}$"

    template: str | None = owned_by(Source.SHIKIGAMI, default=None)  # nuwa/<name> or a git URL


class Workload(ItemSpec):
    """A Kubernetes controller, keyed `<cluster>/<namespace>/<kind>/<name>`. Pods are live data, not tracked."""

    type_name = "workload"
    key_pattern: ClassVar[str] = rf"^{_NAME}/{_NAME}/(deployment|statefulset|daemonset|cronjob)/[a-z0-9.-]+$"


class Image(ItemSpec):
    """A container image, keyed by digest: zot.clockmost.com/meru/echo@sha256:..."""

    type_name = "image"
    key_pattern: ClassVar[str] = r"^[a-z0-9.:-]+(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$"


class Volume(ItemSpec):
    """Persistent storage, keyed `k8s/<cluster>/<namespace>/<claim>` or `libvirt/<host>/<disk>`."""

    type_name = "volume"
    key_pattern: ClassVar[str] = rf"^(k8s/{_NAME}/{_NAME}/[a-z0-9.-]+|libvirt/{_NAME}/[A-Za-z0-9._-]+)$"


class Route(ItemSpec):
    """A hostname Caddy serves."""

    type_name = "route"
    key_pattern: ClassVar[str] = r"^([a-z0-9-]+\.)+[a-z]{2,}$"

    auth: Literal["authentik", "none"] | None = owned_by(Source.SHIKIGAMI, Source.CADDY, default=None)
    tls: bool | None = owned_by(Source.CADDY, default=None)


class Database(ItemSpec):
    """A database in a CNPG cluster, keyed `<cluster>/<namespace>/<cnpg cluster>/<database>`."""

    type_name = "database"
    key_pattern: ClassVar[str] = rf"^{_NAME}/{_NAME}/{_NAME}/[a-z0-9_]+$"


class Repo(ItemSpec):
    """A git repository, keyed by its URL without scheme: git.clockmost.com/Meru/thoth."""

    type_name = "repo"
    key_pattern: ClassVar[str] = r"^git\.clockmost\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"

    private: bool | None = owned_by(Source.GITEA, default=None)


class SecretSet(ItemSpec):
    """A set of secrets in Infisical, keyed `infisical/<project>/<environment>[/<path>]`. Never the values."""

    type_name = "secret-set"
    key_pattern: ClassVar[str] = r"^infisical/[a-z0-9-]+/[a-z0-9-]+(/[A-Za-z0-9._-]+)*$"


class SsoProvider(ItemSpec):
    """An Authentik provider (OIDC client or forward-auth), keyed authentik/<slug>."""

    type_name = "sso-provider"
    key_pattern: ClassVar[str] = rf"^authentik/{_NAME}$"


ITEM_SPECS: tuple[type[ItemSpec], ...] = (
    Person, Bot, Group, Account,
    Host, Device, Network, Cluster,
    App, Workload, Image, Volume, Route, Database, Repo, SecretSet, SsoProvider,
)  # fmt: skip
