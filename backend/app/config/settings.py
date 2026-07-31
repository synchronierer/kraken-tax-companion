from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = Field(default="development", alias="ENV")
    log_level: str = "INFO"
    database_url: str = "sqlite:///./kraken-tax-companion.db"
    cors_origins: list[str] = ["http://localhost:5173"]
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str | None = None
    coingecko_api_mode: str = "disabled"
    coingecko_timeout_seconds: int = 15
    max_upload_bytes: int = 5_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
