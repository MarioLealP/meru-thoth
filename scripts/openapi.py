"""Write the API's OpenAPI spec to openapi.json (committed, so every PR shows how the contract changed).

uv run scripts/openapi.py           write it
uv run scripts/openapi.py --check   fail if the committed copy is stale
"""

import json
import sys
from pathlib import Path

from thoth.api import app

SPEC = Path(__file__).parent.parent / "openapi.json"

spec = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
if "--check" in sys.argv[1:]:
    if not SPEC.exists() or SPEC.read_text() != spec:
        sys.exit("openapi.json is stale - run: uv run scripts/openapi.py")
else:
    SPEC.write_text(spec)
