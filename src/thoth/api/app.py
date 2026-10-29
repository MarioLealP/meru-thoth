from importlib.metadata import version

from fastapi import FastAPI
from pydantic import BaseModel, Field

from thoth.api import problems

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
    openapi_tags=[{"name": "health", "description": "Is Thoth up."}],
    responses={"default": problems.PROBLEM_RESPONSE},
)
problems.install(app)


class Heartbeat(BaseModel):
    """The process is alive."""

    status: str = Field(default="ok", examples=["ok"])


@app.get("/heartbeat", tags=["health"], operation_id="heartbeat")
def heartbeat() -> Heartbeat:
    """Liveness: the process answers. Says nothing about the database (readiness will)."""
    return Heartbeat()
