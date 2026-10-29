"""What each source's scope may cover (D52): a snapshot is refused if it reports something outside it.

The scope is where the source looked. Sources that see a whole system have one scope, `all`. Rules follow the key
formats in items.py; they get revisited when each scanner is built against the real system (D51).
"""

import re
from collections.abc import Callable
from typing import Any

from thoth.catalog.base import Source

ALL = "all"
SCOPE_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"

Rule = Callable[[str, str, str, dict[str, Any]], bool]  # (scope, type, key, attributes) -> in scope?


def _host_scan(scope: str, type_name: str, key: str, attributes: dict[str, Any]) -> bool:
    """One machine: itself, its VMs (a hypervisor sees them), its Linux accounts and groups, its VM disks."""
    match type_name:
        case "host":
            return key == scope or attributes.get("kind") == "vm"
        case "account" | "group":
            return key.startswith(f"linux/{scope}/")
        case "volume":
            return key.startswith(f"libvirt/{scope}/")
    return False


def _k8s_scan(scope: str, type_name: str, key: str, attributes: dict[str, Any]) -> bool:
    """One cluster: itself, what runs in it, and the images it pulls (images are shared, keyed by registry)."""
    match type_name:
        case "cluster":
            return key == scope
        case "workload" | "database":
            return key.startswith(f"{scope}/")
        case "volume" | "account" | "group":
            return key.startswith(f"k8s/{scope}/")
        case "network":
            return key == f"k8s/{scope}"
        case "image":
            return True
    return False


RULES: dict[str, Rule] = {Source.HOST: _host_scan, Source.K8S: _k8s_scan}


def scope_problem(source: str, scope: str) -> str | None:
    if not re.fullmatch(SCOPE_PATTERN, scope):
        return f"scope {scope!r} isn't a name like clockmost-desktop"
    if source not in RULES and scope != ALL:
        return f"{source} sees a whole system - its scope is {ALL!r}"
    return None


def in_scope(source: str, scope: str, type_name: str, key: str, attributes: dict[str, Any]) -> bool:
    rule = RULES.get(source)
    return rule is None or rule(scope, type_name, key, attributes)
