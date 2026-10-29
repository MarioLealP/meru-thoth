"""Write the generated contracts, committed so every PR shows how they changed:
openapi.json (the API) and catalog.json (the item and relation types).

    uv run scripts/generate.py           write them
    uv run scripts/generate.py --check   fail if a committed copy is stale
"""

import json
import sys
from pathlib import Path

from thoth import catalog
from thoth.api import app

ROOT = Path(__file__).parent.parent
CONTRACTS = {"openapi.json": app.openapi(), "catalog.json": catalog.export()}

stale = []
for name, data in CONTRACTS.items():
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    path = ROOT / name
    if "--check" not in sys.argv[1:]:
        path.write_text(text)
    elif not path.exists() or path.read_text() != text:
        stale.append(name)
if stale:
    sys.exit(f"stale: {', '.join(stale)} - run: uv run scripts/generate.py")
