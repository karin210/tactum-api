from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Only what is needed to reach PostgreSQL, so migrations and scripts don't need Stripe keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Plain libpq URL: postgresql://user:password@host:5432/dbname
    database_url: str
    # "require" encrypts the connection; "verify-full" also checks the server certificate.
    database_ssl: Literal["disable", "require", "verify-full"] = "disable"
    # Path to the AWS RDS CA bundle (global-bundle.pem), used with "verify-full".
    database_ssl_root_cert: str | None = None


class Settings(DatabaseSettings):
    environment: Literal["development", "production"] = "development"
    # Comma-separated list, e.g. "https://tactum.mx,https://www.tactum.mx"
    cors_origins: str = "http://localhost:3000"

    stripe_secret_key: str
    stripe_webhook_secret: str

    # Prices live on the server so the client can never choose what it pays.
    product_total_price_mxn: int = 875
    deposit_amount_mxn: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
