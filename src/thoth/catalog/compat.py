"""Which catalog changes break existing data - the catalog's oasdiff.

Breaking: a type removed, a key pattern changed, a relation end narrowed, a field removed, a field's type changed, a
field newly required, a field's owners changed. Not breaking: new types, new optional fields, wider relation ends,
descriptions. A breaking change needs a data migration (or a reason it doesn't), acknowledged in the PR with a
`Catalog-Break: <why>` commit trailer.
"""

from typing import Any

# Schema keywords that describe a field without constraining its data.
_NOT_SHAPE = {"description", "title", "examples", "default", "x-owners"}


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    found = []
    for kind in ("items", "relations"):
        for name, before in old[kind].items():
            where = f"{kind.removesuffix('s')} type {name}"
            after = new[kind].get(name)
            if after is None:
                found.append(f"{where}: removed")
                continue
            if kind == "items" and before["key_pattern"] != after["key_pattern"]:
                found.append(f"{where}: key pattern changed")
            if kind == "relations":
                for end in ("from", "to"):
                    lost = set(before[end]) - set(after[end])
                    if lost and "*" not in after[end]:
                        found.append(f"{where}: no longer allows {', '.join(sorted(lost))} at the {end} end")
            found += [f"{where}: {change}" for change in _field_changes(before["schema"], after["schema"])]
    return found


def _field_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    old_fields, new_fields = old.get("properties", {}), new.get("properties", {})
    newly_required = set(new.get("required", [])) - set(old.get("required", []))
    found = [f"field {f} removed" for f in sorted(old_fields.keys() - new_fields.keys())]
    found += [
        f"field {f} is now required" if f in old_fields else f"new field {f} is required"
        for f in sorted(newly_required)
    ]
    for f in sorted(old_fields.keys() & new_fields.keys()):
        before, after = old_fields[f], new_fields[f]
        if before.get("x-owners") != after.get("x-owners"):
            found.append(f"field {f} owners changed: {before.get('x-owners')} -> {after.get('x-owners')}")
        if _shape(before) != _shape(after):
            found.append(f"field {f} type changed")
    return found


def _shape(field: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in field.items() if k not in _NOT_SHAPE}
