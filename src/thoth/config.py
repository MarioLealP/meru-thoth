"""Settings from the environment (THOTH_*), via pydantic-settings."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="THOTH_")

    # postgresql+psycopg://user:password@host:5432/thoth - a secret, it carries the password.
    database_url: SecretStr


def get_settings() -> Settings:
    return Settings()  # filled from the environment
