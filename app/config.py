"""Application settings — loaded from environment variables / .env file."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase — not required for Phase 1 local dev
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")  # Backend only, never exposed

    # Application
    app_env: str = Field(default="development")
    app_debug: bool = Field(default=False)
    app_secret_key: str = Field(default="change-me-in-production")

    # Phase 3+ (not used in Phase 1)
    groq_api_key: str = Field(default="")
    e2b_api_key: str = Field(default="")

    # Resource limits
    max_repo_size_mb: int = Field(default=100)
    max_run_duration_seconds: int = Field(default=300)
    max_concurrent_runs: int = Field(default=5)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def is_supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
