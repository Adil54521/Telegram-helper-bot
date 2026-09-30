from functools import lru_cache
from decimal import Decimal

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
    log_dir: str = "logs"
    log_max_bytes: int = Field(default=20_971_520, gt=0)
    log_backup_count: int = Field(default=10, ge=1, le=100)
    ai_enabled: bool = False
    ai_provider: str = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = ""
    ai_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ai_cooldown_seconds: float = Field(default=8.0, ge=0, le=300)
    gemini_input_price_per_1m: Decimal = Field(default=Decimal("0.30"), ge=0)
    gemini_output_price_per_1m: Decimal = Field(default=Decimal("2.50"), ge=0)

    @property
    def database_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return f"postgresql+asyncpg://{self.postgres_user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def ai_available(self) -> bool:
        return bool(self.ai_enabled and self.ai_provider == "gemini" and self.gemini_api_key and self.gemini_api_key.get_secret_value() and self.gemini_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
