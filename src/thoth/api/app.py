"""The FastAPI app: the probes, the catalog route and the /v1 routers."""

from importlib.metadata import version
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from thoth import catalog
from thoth.api import problems
from thoth.api.deps import EngineDep, Reader
from thoth.api.items import router as items_router
from thoth.api.snapshots import router as snapshots_router
from thoth.catalog import check

app = FastAPI(
    title="Thoth",
    summary="Meru's CMDB: what exists, how it connects, and how it got that way.",
    description=(
        "Items (hosts, apps, accounts, volumes...), the typed relations between them, and every earlier version of"
        " both. The only way to write to Thoth: each write names an actor and a reason, and nothing is deleted."
    ),
    version=version("thoth"),
    license_info={"name": "MIT", "identifier": "MIT"},
    servers=[{"url": "https://thoth.clockmost.com", "description": "clockmost"}],
    openapi_tags=[
        {"name": "health", "description": "Is Thoth up."},
        {"name": "catalog", "description": "Which item and relation types exist, and what they hold."},
        {"name": "items", "description": "The inventory: items, their links and their history."},
        {"name": "scans", "description": "Sources report what they see; Thoth works out what changed."},
    ],
    responses={"default": problems.PROBLEM_RESPONSE},
)
problems.install(app)
app.state.catalog_checked = False

# Every client-facing route lives under /v1: a future /v2 can change shapes while /v1 keeps working. Only the
# probes (/heartbeat, /ready) sit outside - they're for Kubernetes and never change.
v1 = APIRouter(prefix="/v1")


class Health(BaseModel):
    """Thoth's state."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(default="ok", examples=["ok"])


@app.get("/heartbeat", tags=["health"], operation_id="heartbeat", status_code=200)
def heartbeat() -> Health:
    """Liveness: the process answers. Says nothing about the database."""
    return Health()


@app.get("/ready", tags=["health"], operation_id="ready", status_code=200)
def ready(engine: EngineDep) -> Health:
    """Readiness: the database answers, and (checked once per process) every stored row fits this version's catalog.

    Only then is the catalog copied into its tables - so a version whose catalog change left misfit rows never
    goes live, and the old one keeps serving.
    """
    try:
        with Session(engine) as session:
            session.connection().execute(text("select 1"))
            if not app.state.catalog_checked:
                if found := check.problems(session):
                    shown = "; ".join(found[:20]) + (f"; and {len(found) - 20} more" if len(found) > 20 else "")
                    raise HTTPException(503, f"{len(found)} stored rows don't fit the catalog: {shown}")
                check.sync(session)
                session.commit()
                app.state.catalog_checked = True
    except OperationalError as e:
        raise HTTPException(503, "the database doesn't answer") from e
    return Health()


class ItemTypeOut(BaseModel):
    """An item type."""

    model_config = ConfigDict(frozen=True)

    description: str
    key_pattern: str = Field(examples=["^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"])
    schema_: dict[str, Any] = Field(alias="schema", examples=[{"properties": {"os": {"x-owners": ["host-scan"]}}}])


class RelationTypeOut(BaseModel):
    """A relation type: which item types may sit at each end (`*` = any)."""

    model_config = ConfigDict(frozen=True)

    description: str
    from_: list[str] = Field(alias="from", examples=[["workload"]])
    to: list[str] = Field(examples=[["volume"]])
    schema_: dict[str, Any] = Field(alias="schema", examples=[{"properties": {"path": {"x-owners": ["k8s-scan"]}}}])


_HOST: JsonValue = {"description": "An operating system instance.", "key_pattern": "^[a-z0-9-]+$", "schema": {}}
_MOUNTS: JsonValue = {
    "description": "A workload mounts a volume.",
    "from": ["workload"],
    "to": ["volume"],
    "schema": {},
}


class Catalog(BaseModel):
    """Every item and relation type, by name."""

    model_config = ConfigDict(
        frozen=True, json_schema_extra={"examples": [{"items": {"host": _HOST}, "relations": {"mounts": _MOUNTS}}]}
    )

    items: dict[str, ItemTypeOut] = Field(examples=[{"host": _HOST}])
    relations: dict[str, RelationTypeOut] = Field(examples=[{"mounts": _MOUNTS}])


@v1.get("/catalog", tags=["catalog"], operation_id="get_catalog", status_code=200, response_model_by_alias=True)
def get_catalog(_: Reader) -> Catalog:
    """Every item and relation type.

    For each: description, key pattern (items), allowed ends (relations), and the JSON Schema of its attributes - each
    field's `x-owners` lists the sources that may report it, first one wins.
    """
    return Catalog.model_validate(catalog.export())


v1.include_router(items_router)
v1.include_router(snapshots_router)
app.include_router(v1)
