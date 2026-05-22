from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Supabase Postgres — see .env.example for the two URL formats.
    database_url: str = ""

    anthropic_api_key: str = ""
    fred_api_key: str = ""

    log_level: str = "INFO"
    rss_user_agent: str = "NewsCredibilityBot/0.1 (+https://example.com/about)"
    rss_rate_limit_sec: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
