"""What routes get injected - overridden in tests."""

import logging
from collections.abc import Callable
from functools import cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine

from thoth.config import get_settings
from thoth.core.auth import Caller, UnauthenticatedError, Verifier
from thoth.db.session import make_engine

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False, description="An Authentik access token (client credentials).")
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@cache
def get_engine() -> Engine:
    """The process's one engine, built on first use."""
    return make_engine()


@cache
def get_verifier() -> Verifier:
    """The process's one token verifier, built on first use."""
    settings = get_settings()
    return Verifier(settings=settings, keys=jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=300))


def get_caller(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    verifier: Annotated[Verifier, Depends(get_verifier)],
) -> Caller:
    """Who is calling, or a 401 with a Bearer challenge."""
    if credentials is None:
        raise HTTPException(401, "a bearer token is required", headers=_CHALLENGE)
    try:
        return verifier.verify(credentials.credentials)
    except UnauthenticatedError as e:
        log.info("token refused: %s", e)  # the reason stays in the log, not in the answer
        raise HTTPException(401, "the token isn't accepted", headers=_CHALLENGE) from e


def require(permission: str) -> Callable[[Caller], Caller]:
    """A dependency that answers 403 unless the caller holds `permission`."""

    def check(caller: Annotated[Caller, Depends(get_caller)]) -> Caller:
        if not caller.may(permission):
            raise HTTPException(403, f"needs {permission}")
        return caller

    return check


CallerDep = Annotated[Caller, Depends(get_caller)]
EngineDep = Annotated[Engine, Depends(get_engine)]
Reader = Annotated[Caller, Depends(require("thoth.read"))]
