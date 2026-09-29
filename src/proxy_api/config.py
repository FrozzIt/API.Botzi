from functools import lru_cache
from pathlib import Path

from pydantic import ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore")

    client_config_path: Path
    database_url: str
    provider_accounts: str
    redis_url: str
    login_attempt_limit: int = Field(default=5, gt=0)
    login_attempt_window_seconds: int = Field(default=60, gt=0)
    lptracker_base_url: str = "https://direct.lptracker.ru"
    lptracker_account: str = "primary"
    lptracker_login: SecretStr | None = None
    lptracker_password: SecretStr | None = None
    lptracker_service: SecretStr | None = None
    lptracker_version: str = "1"
    provider_deadline_seconds: float = Field(default=5.0, gt=0)
    provider_http_timeout_seconds: float = Field(default=3.0, gt=0)
    provider_quota_limit: int = Field(default=3, gt=0, le=3)
    provider_token_encryption_key: SecretStr | None = None
    provider_token_ttl_seconds: int = Field(default=3_600, gt=0)
    session_idle_timeout_seconds: int = 86_400

    @property
    def allowed_provider_accounts(self) -> frozenset[str]:
        accounts = frozenset(
            value.strip() for value in self.provider_accounts.split(",") if value.strip()
        )
        if not accounts:
            raise ValueError("PROVIDER_ACCOUNTS must contain at least one account reference")
        return accounts


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
