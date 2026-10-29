"""How the catalog is written: one Pydantic class per item or relation type, every field tagged with its owners.

    class Route(ItemSpec):
        \"\"\"A hostname Caddy serves.\"\"\"
        type_name = "route"
        key_pattern = r"^...$"
        auth: Literal["authentik", "none"] | None = owned_by(Source.SHIKIGAMI, Source.CADDY, default=None)

Owners are in precedence order: the item's value for a field comes from the first owner that reported it. Each
source may only send the fields it owns (its slice); a field that is required is required in that slice - the owner
must always send it. Declared-vs-observed drift compares the slices, not the merged value.
"""

import re
from copy import copy
from datetime import timedelta
from enum import StrEnum
from functools import cache
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import PydanticUndefined


class Source(StrEnum):
    """Everything that may write to Thoth. Each writes with its own identity, and only its slice."""

    SHIKIGAMI = "shikigami"  # what was requested (Meru apps and their parts)
    MANUAL = "manual"  # corrections and things no scanner sees, via the atlas
    AUTHENTIK = "authentik-sync"
    GITEA = "gitea-sync"
    HOST = "host-scan"  # OS facts, Linux accounts, libvirt VMs
    K8S = "k8s-scan"
    ROUTER = "r7000-scan"
    CADDY = "caddy-scan"


ANY_TYPE = "*"


def owned_by(*sources: Source, **field: Any) -> Any:
    """A catalog field, with the sources that may report it - first one wins."""
    if not sources:
        raise ValueError("a field needs at least one owner")
    return Field(json_schema_extra={"x-owners": [s.value for s in sources]}, **field)


class Spec(BaseModel):
    """What item and relation types share: strict attributes, every field owned, a docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type_name: ClassVar[str]
    # Types whose presence comes and goes (a phone, the desktop on the LAN): go to Anubis only after this long unseen,
    # instead of after 3 scans without them (D54).
    missing_after: ClassVar[timedelta | None] = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if "type_name" not in cls.__dict__:
            return  # an abstract base
        if not cls.__doc__:
            raise TypeError(f"{cls.__name__} needs a docstring - it becomes the type's description")
        for name in cls.model_fields:
            if not cls.owners(name):
                raise TypeError(f"{cls.__name__}.{name} has no owner - declare it with owned_by()")

    @classmethod
    def owners(cls, field: str) -> list[str]:
        extra = cls.model_fields[field].json_schema_extra
        return list(extra.get("x-owners", [])) if isinstance(extra, dict) else []  # ty: ignore[invalid-argument-type]

    @classmethod
    def description(cls) -> str:
        return " ".join((cls.__doc__ or "").split())

    @classmethod
    def slice_for(cls, source: str, *, strict: bool = False) -> type[BaseModel]:
        """The model a source's slice is validated against: only the fields it owns.

        strict (a new snapshot, D51): every owned field must be present - `null` where empty - since a missing field
        means the scanner broke. Not strict (stored slices): fields added to the catalog since may be absent."""
        return _slice(cls, source, strict)

    @classmethod
    def merge(cls, slices: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Build the item's attributes from every source's slice, by field precedence."""
        merged = {}
        for name in cls.model_fields:
            for owner in cls.owners(name):
                if name in slices.get(owner, {}):
                    merged[name] = slices[owner][name]
                    break
        return merged


@cache
def _slice(spec: type[Spec], source: str, strict: bool) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for name, info in spec.model_fields.items():
        if source in spec.owners(name):
            if strict:
                info = copy(info)
                info.default, info.default_factory = PydanticUndefined, None
            fields[name] = (info.annotation, info)
    name = f"{spec.__name__}From{source.title().replace('-', '')}{'Strict' if strict else ''}"
    return create_model(name, __config__=spec.model_config, **fields)


class ItemSpec(Spec):
    key_pattern: ClassVar[str]  # every source must spell keys this way, so they meet on the same item

    @classmethod
    def key_ok(cls, key: str) -> bool:
        return re.fullmatch(cls.key_pattern, key) is not None


class RelationSpec(Spec):
    from_types: ClassVar[frozenset[str]]  # the dependent end; ANY_TYPE allows every item type
    to_types: ClassVar[frozenset[str]]
