"""Application settings — loaded from environment variables / .env file."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Supabase
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")  # Backend only, NEVER exposed
    supabase_jwt_secret: str = Field(default="")        # Phase 2: JWT verification

    # Application
    app_env: str = Field(default="development")
    app_debug: bool = Field(default=False)
    app_secret_key: str = Field(default="change-me-in-production")

    # Phase 3+
    groq_api_key: str = Field(default="")
    e2b_api_key: str = Field(default="")
    e2b_template_id: str = Field(default="")       # Optional custom E2B template
    e2b_timeout_seconds: int = Field(default=300)  # Max sandbox lifetime (seconds)

    # Storage
    evidence_bucket: str = Field(default="evidence-artifacts")

    # Resource limits
    max_repo_size_mb: int = Field(default=100)
    max_extracted_size_mb: int = Field(default=200)
    max_repo_files: int = Field(default=5000)
    max_run_duration_seconds: int = Field(default=300)
    max_concurrent_runs: int = Field(default=5)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def is_supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def is_jwt_configured(self) -> bool:
        return bool(self.supabase_jwt_secret)

    def is_development(self) -> bool:
        return self.app_env == "development"

    def is_e2b_configured(self) -> bool:
        """Return True if E2B API key is set — required for real sandbox execution."""
        return bool(self.e2b_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
