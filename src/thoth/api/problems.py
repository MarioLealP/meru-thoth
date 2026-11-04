"""Every error the API returns has one shape: RFC 9457 problem details (application/problem+json).

Clients - Shikigami, scanners, the atlas - parse one format instead of FastAPI's two (HTTPException's `detail` and
validation errors' list).
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    """What went wrong (RFC 9457 problem details)."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(default="about:blank", examples=["about:blank"])
    title: str = Field(examples=["Not Found"])
    status: int = Field(examples=[404])
    detail: str | None = Field(default=None, examples=["no host with key 'clockmost'"])
    errors: list[dict[str, Any]] | None = Field(  # field-level details, for validation failures
        default=None, examples=[[{"loc": ["body", "key"], "msg": "Field required", "type": "missing"}]]
    )


class ProblemError(Exception):
    """Raise to answer with a full problem (e.g. with field-level `errors`), not just a status and detail."""

    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.title)
        self.problem = problem


def _respond(problem: Problem, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        problem.model_dump(exclude_none=True), status_code=problem.status, media_type=MEDIA_TYPE, headers=headers
    )


def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    problem = Problem(title=HTTPStatus(exc.status_code).phrase, status=exc.status_code, detail=exc.detail)
    return _respond(problem, getattr(exc, "headers", None))


def _problem_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)
    return _respond(exc.problem)


def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return _respond(Problem(title="Unprocessable Content", status=422, detail="the request is invalid", errors=errors))


def install(app: FastAPI) -> None:
    """Make `app` answer every error as problem details."""
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(ProblemError, _problem_error)


# For `responses=` on routes, so the OpenAPI spec documents the shape.
PROBLEM_RESPONSE: dict[str, Any] = {
    "model": Problem,
    "description": "Any error, as problem details.",
    "content": {MEDIA_TYPE: {"example": {"type": "about:blank", "title": "Not Found", "status": 404}}},
}
