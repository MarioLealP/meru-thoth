"""Fetch the pinned contract checkers into .tools/bin and print that directory.

The same binaries run locally and in CI: each release archive is checked against its sha256 before use.
"""

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import httpx2

TOOLS = {
    "vacuum": (  # v0.35.7
        "https://github.com/daveshanley/vacuum/releases/download/v0.35.7/vacuum_0.35.7_linux_x86_64.tar.gz",
        "310879b6f2048cf8e5d17eb72d6b4121e02fb29fd6dad50bfab5fd1f4218de77",
    ),
    "oasdiff": (  # v1.35.0
        "https://github.com/oasdiff/oasdiff/releases/download/v1.35.0/oasdiff_1.35.0_linux_amd64.tar.gz",
        "a8023002fcb2eaa7f5758148a9303cb63d33b939886df312268bc0fc7b939140",
    ),
}

BIN = Path(__file__).resolve().parent.parent / ".tools" / "bin"


def fetch(name: str, url: str, sha256: str) -> None:
    """Put one tool in BIN, unless the right version is already there."""
    stamp = BIN / f".{name}.sha256"
    if (BIN / name).exists() and stamp.exists() and stamp.read_text() == sha256:
        return
    response = httpx2.get(url, timeout=60, follow_redirects=True)  # GitHub answers release downloads with a redirect
    response.raise_for_status()
    archive = response.content
    if hashlib.sha256(archive).hexdigest() != sha256:
        sys.exit(f"{name}: checksum mismatch for {url}")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        member = tar.extractfile(name)
        if member is None:
            sys.exit(f"{name}: not in {url}")
        target = BIN / name
        target.write_bytes(member.read())
    target.chmod(0o755)
    stamp.write_text(sha256)


def main() -> None:
    """Fetch every tool and print where they are."""
    BIN.mkdir(parents=True, exist_ok=True)
    for name, (url, sha256) in TOOLS.items():
        fetch(name, url, sha256)
    print(BIN)


if __name__ == "__main__":
    main()
