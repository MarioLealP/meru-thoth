"""Applying scans: each source's whole picture from one scope, all or nothing (D38, D51-D54).

A scan run (a Temporal workflow, D54) sends every source's items first, then every source's relations, each tagged
with the scan id - so links to another source's items find both ends, and resending a snapshot changes nothing.
"""

from thoth.snapshots.common import SCAN_ID_PATTERN, SnapshotRefusedError, Summary, current_slices
from thoth.snapshots.items import ReportedItem, apply_items
from thoth.snapshots.relations import ReportedEnd, ReportedRelation, apply_relations

__all__ = [
    "SCAN_ID_PATTERN",
    "ReportedEnd",
    "ReportedItem",
    "ReportedRelation",
    "SnapshotRefusedError",
    "Summary",
    "apply_items",
    "apply_relations",
    "current_slices",
]
