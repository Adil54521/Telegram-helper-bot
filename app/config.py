from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    postgres_db: str = "telegram_bot"
    postgres_user: str = "telegram_bot"
    postgres_password: SecretStr
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    super_admin_id: int = Field(gt=0)
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return f"postgresql+asyncpg://{self.postgres_user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

