"""Thoth's settings, read from THOTH_* environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """What Thoth reads from its environment."""

    model_config = SettingsConfigDict(env_prefix="THOTH_", frozen=True)

    # postgresql+psycopg://user:password@host:5432/thoth - a secret, it carries the password.
    database_url: SecretStr

    # Accepting a token (security standard section 5): Authentik's issuer and keys, and Thoth's own audience.
    issuer: str  # https://auth.clockmost.com/application/o/thoth/
    audience: str = "thoth"
    jwks_url: str  # https://auth.clockmost.com/application/o/thoth/jwks/
    algorithms: list[str] = ["RS256", "ES256"]
    leeway_seconds: int = 30  # clock tolerance - counts toward the revocation window
    permissions_claim: str = "scope"  # space-separated; which claim Authentik fills is confirmed at deploy
    name_claim: str = "preferred_username"  # the bot's name, recorded as changed_by


def get_settings() -> Settings:
    """Read the settings from the environment."""
    return Settings()  # filled from the environment
