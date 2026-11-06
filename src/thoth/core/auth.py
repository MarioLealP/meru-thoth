"""Who is calling, and what they may do (D56).

Callers send an Authentik access token (client credentials, 5 minutes). Thoth accepts it only if everything in the
security standard's section 5 holds - trusted issuer, its own audience, an allowed algorithm and a key from Authentik's
JWKS, within expiry/not-before (small leeway), required claims present - and then checks the permission the route
needs. Authentik is the one place that grants permissions:

    thoth.read                          read anything
    thoth.report:<source>@<scope>       send that source's scans for that scope (and rename what it reported)
    thoth.manual                        corrections and renames by hand (Mario, via the atlas)
    thoth.verdict                       retire things (Anubis) - the database checks it too
    thoth.retire                        mark an app decommissioning, then retained (Shikigami's retire pass, D85),
                                        or planned again (a restore, D106) - the database checks it too

Revocation model: local validation until expiry; a disabled bot stops within the token lifetime + leeway.
"""

from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict

from thoth.config import Settings

READ, MANUAL, VERDICT, RETIRE = "thoth.read", "thoth.manual", "thoth.verdict", "thoth.retire"


def report(source: str, scope: str) -> str:
    """The permission to send `source`'s scans for `scope`."""
    return f"thoth.report:{source}@{scope}"


class Caller(BaseModel):
    """An accepted token: who it names and what it may do."""

    model_config = ConfigDict(frozen=True)

    name: str  # recorded as changed_by
    permissions: frozenset[str]

    def may(self, permission: str) -> bool:
        """Whether the caller holds exactly this permission."""
        return permission in self.permissions

    def reports_as(self, source: str) -> bool:
        """Whether the caller may report as `source`, from any scope."""
        return any(p.startswith(f"thoth.report:{source}@") for p in self.permissions)


class UnauthenticatedError(Exception):
    """No token, or one Thoth doesn't accept - the reason is logged, never sent back in detail."""


class Verifier(BaseModel):
    """Accepts or refuses tokens."""

    model_config = ConfigDict(frozen=True)

    settings: Settings
    keys: Any  # Authentik's JWKS, fetched and cached; tests hand in a key source of their own.

    def verify(self, token: str) -> Caller:
        """The token's caller; raises if Thoth doesn't accept the token."""
        s = self.settings
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=s.algorithms,
                issuer=s.issuer,
                audience=s.audience,
                leeway=s.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise UnauthenticatedError(str(e)) from e
        raw = claims.get(s.permissions_claim, "")
        permissions = frozenset(raw.split() if isinstance(raw, str) else raw)
        return Caller(name=str(claims.get(s.name_claim) or claims["sub"]), permissions=permissions)
