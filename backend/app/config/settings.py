from datetime import UTC, datetime
from functools import lru_cache

from pydantic import Field, field_validator
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
    kraken_api_key: str | None = None
    kraken_api_secret: str | None = None
    kraken_api_base_url: str = "https://api.kraken.com"
    kraken_api_timeout: int = 15
    kraken_api_max_retries: int = 2
    kraken_sync_initial_start: datetime = datetime(2020, 1, 1, tzinfo=UTC)
    kraken_sync_lookback_seconds: int = Field(default=604800, gt=0, le=7776000)
    kraken_sync_settlement_lag_seconds: int = Field(default=300, ge=0, le=86400)
    kraken_sync_stale_seconds: int = Field(default=3600, gt=0, le=604800)
    max_upload_bytes: int = 5_000_000
    export_directory: str = "/exports"

    @field_validator("kraken_sync_initial_start")
    @classmethod
    def validate_sync_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Kraken sync initial start must include a timezone.")
        return value.astimezone(UTC)


@lru_cache
def get_settings() -> Settings:
    return Settings()
