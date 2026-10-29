"""Tokens for tests: a local signing key standing in for Authentik's, checked by Thoth's real verifier."""

import time
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from thoth.auth import MANUAL, READ, report
from thoth.catalog import Source
from thoth.config import Settings

ISSUER = "https://auth.test/application/o/thoth/"
KID = "test-key"
_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)

SETTINGS = Settings(
    database_url="postgresql+psycopg://unused",
    issuer=ISSUER,
    jwks_url="https://auth.test/unused",
)
ALL_TEST_PERMISSIONS = [READ, MANUAL] + [
    report(source, scope) for source in Source for scope in ("all", "clockmost", "clockmost-desktop")
]


class _Keys:
    """Like PyJWKClient: only the one known key id is accepted."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        if jwt.get_unverified_header(token).get("kid") != KID:
            raise jwt.PyJWKClientError("unknown signing key")
        return SimpleNamespace(key=_PRIVATE.public_key())


KEYS = _Keys()


def mint(*permissions: str, name: str = "svc+test", key: Any = _PRIVATE, algorithm: str = "RS256",
         kid: str = KID, **claims: Any) -> str:  # fmt: skip
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": "thoth", "sub": "1234", "iat": now, "exp": now + 300,
               "preferred_username": name, "scope": " ".join(permissions), **claims}  # fmt: skip
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": kid})


def bearer(*permissions: str, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(*permissions, **kwargs)}"}
