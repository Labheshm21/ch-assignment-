from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    token_encryption_key: str = ""
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./chronos.db"

    google_client_id: str = ""
    google_client_secret: str = ""

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "moonshotai/kimi-k3"

    worker_poll_seconds: int = Field(default=5, ge=1, le=60)
    worker_lock_timeout_minutes: int = Field(default=5, ge=1, le=60)

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.backend_url.rstrip('/')}/auth/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()

