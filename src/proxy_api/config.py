from functools import lru_cache
from pathlib import Path

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore")

    client_config_path: Path
    database_url: str
    provider_accounts: str
    redis_url: str
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
