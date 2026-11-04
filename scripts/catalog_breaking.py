"""Fail on breaking catalog changes against a base ref, unless a commit acknowledges them.

    uv run scripts/catalog_breaking.py origin/main

Acknowledge with a commit trailer in the PR: `Catalog-Break: <why it's safe / which migration fixes the data>`.
"""

import json
import subprocess
import sys

from thoth.catalog import export
from thoth.catalog.compat import breaking_changes


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git, capturing its output; the caller reads the return code."""
    return subprocess.run(["git", *args], capture_output=True, text=True)


base = sys.argv[1]
old = git("show", f"{base}:catalog.json")
if old.returncode != 0:
    print(f"no catalog.json on {base} yet - nothing to compare")
    sys.exit(0)
found = breaking_changes(json.loads(old.stdout), export())
if not found:
    print("no breaking catalog changes")
    sys.exit(0)
print("breaking catalog changes:", *(f"  {change}" for change in found), sep="\n")
acks = git("log", f"{base}..HEAD", "--format=%(trailers:key=Catalog-Break,valueonly)").stdout.split()
if acks:
    print("acknowledged by a Catalog-Break trailer")
    sys.exit(0)
sys.exit("add a data migration, then a `Catalog-Break: <why>` trailer to a commit in this PR")
