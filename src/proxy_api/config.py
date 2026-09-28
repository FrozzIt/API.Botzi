from functools import lru_cache

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore")

    database_url: str
    redis_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
