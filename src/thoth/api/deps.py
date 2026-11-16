"""What routes get injected - overridden in tests."""

import logging
from collections.abc import AsyncIterator, Callable
from functools import cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from thoth.config import get_settings
from thoth.core.auth import Caller, UnauthenticatedError, Verifier
from thoth.db.session import make_engine
from thoth.models import Item

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False, description="An Authentik access token (client credentials).")
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@cache
def get_engine() -> AsyncEngine:
    """The process's one engine, built on first use."""
    return make_engine()


async def get_session(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> AsyncIterator[AsyncSession]:
    """One session per request; routes commit what they write, the rest is rolled back on the way out."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
CallerDep = Annotated[Caller, Depends(get_caller)]


async def get_item(
    item_id: Annotated[UUID, Path(description="The item's id.", examples=["7d0c1f5e-0b7a-4c55-9a51-2f3c6f0f8a11"])],
    session: SessionDep,
) -> Item:
    """The item the path names, or a 404."""
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"no item {item_id}")
    return item


ItemDep = Annotated[Item, Depends(get_item)]
Reader = Annotated[Caller, Depends(require("thoth.read"))]
