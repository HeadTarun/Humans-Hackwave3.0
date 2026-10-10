"""FastAPI dependency injection helpers.

Auth strategy:
  Phase 1: X-User-ID header (dev/test only)
  Phase 2: Supabase JWT (Authorization: Bearer <token>) with X-User-ID fallback
            in development mode so all Phase 1 tests continue to pass.

Repository selection:
  - If SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set → SupabaseRepository
  - Otherwise → InMemoryRepository (local dev / CI)
"""
from __future__ import annotations

import logging

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWTError

from app.config import Settings, get_settings
from app.persistence.in_memory import InMemoryRepository
from app.persistence.repository import PersistenceRepository
from app.services.test_definition_service import TestDefinitionService
from app.services.test_run_service import TestRunService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repository singleton (overridable for tests)
# ---------------------------------------------------------------------------

_repository: PersistenceRepository | None = None


def get_repository() -> PersistenceRepository:
    global _repository
    if _repository is None:
        settings = get_settings()
        if settings.is_supabase_configured():
            try:
                from app.persistence.supabase_repo import SupabaseRepository
                _repository = SupabaseRepository(
                    settings.supabase_url,
                    settings.supabase_service_role_key,
                )
                logger.info("Using SupabaseRepository")
            except Exception as exc:
                logger.warning(
                    "Failed to init SupabaseRepository, falling back to InMemory: %s", exc
                )
                _repository = InMemoryRepository()
        else:
            _repository = InMemoryRepository()
            logger.info("Using InMemoryRepository (Supabase not configured)")
    return _repository


def set_repository(repo: PersistenceRepository) -> None:
    """Override the singleton. Used by tests for isolation."""
    global _repository
    _repository = repo


def reset_repository() -> None:
    """Reset to auto-select. Called between tests."""
    global _repository
    _repository = None


# ---------------------------------------------------------------------------
# Phase 3 tool singletons (overridable for tests)
# ---------------------------------------------------------------------------

from app.services.interfaces import (  # noqa: E402
    ArtifactStore,
    BrowserRunner,
    FakeArtifactStore,
    FakeBrowserRunner,
    FakeSandboxRunner,
    SandboxRunner,
)

_sandbox_runner: SandboxRunner | None = None
_browser_runner: BrowserRunner | None = None
_artifact_store: ArtifactStore | None = None


def get_sandbox_runner() -> SandboxRunner:
    """Return E2BSandboxRunner if configured, else FakeSandboxRunner."""
    global _sandbox_runner
    if _sandbox_runner is not None:
        return _sandbox_runner
    settings = get_settings()
    if settings.is_e2b_configured():
        from app.services.sandbox_runner import E2BSandboxRunner
        return E2BSandboxRunner(settings.e2b_api_key, settings.e2b_timeout_seconds)
    return FakeSandboxRunner()


def get_browser_runner() -> BrowserRunner:
    """Return PlaywrightBrowserRunner if available, else FakeBrowserRunner."""
    global _browser_runner
    if _browser_runner is not None:
        return _browser_runner
    try:
        from app.services.browser_runner import PlaywrightBrowserRunner
        # Quick import sanity-check: playwright package must actually be importable
        from playwright.async_api import async_playwright  # noqa: F401
        return PlaywrightBrowserRunner()
    except (ImportError, ModuleNotFoundError):
        logger.warning("Playwright not installed; using FakeBrowserRunner")
        return FakeBrowserRunner()


def get_artifact_store() -> ArtifactStore:
    """Return SupabaseArtifactStore if configured, else FakeArtifactStore."""
    global _artifact_store
    if _artifact_store is not None:
        return _artifact_store
    settings = get_settings()
    if settings.is_supabase_configured():
        from app.services.artifact_store import SupabaseArtifactStore
        return SupabaseArtifactStore(
            settings.supabase_url,
            settings.supabase_service_role_key,
            settings.evidence_bucket,
        )
    return FakeArtifactStore()


def set_sandbox_runner(runner: SandboxRunner) -> None:
    """Override the sandbox runner singleton. Used by tests."""
    global _sandbox_runner
    _sandbox_runner = runner


def set_browser_runner(runner: BrowserRunner) -> None:
    """Override the browser runner singleton. Used by tests."""
    global _browser_runner
    _browser_runner = runner


def set_artifact_store(store: ArtifactStore) -> None:
    """Override the artifact store singleton. Used by tests."""
    global _artifact_store
    _artifact_store = store


def reset_execution_tools() -> None:
    """Reset all execution tool singletons. Called between tests."""
    global _sandbox_runner, _browser_runner, _artifact_store
    _sandbox_runner = None
    _browser_runner = None
    _artifact_store = None


# ---------------------------------------------------------------------------
# JWT verification helper
# ---------------------------------------------------------------------------

def _verify_supabase_jwt(token: str, jwt_secret: str) -> str:
    """Verify a Supabase-issued JWT and return the user's `sub` (uid).

    Supabase uses HS256 with the project JWT secret.
    Raises HTTPException(401) on any verification failure.
    """
    try:
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_exp": True},
        )
        sub: str | None = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="JWT missing 'sub' claim")
        return sub
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


# ---------------------------------------------------------------------------
# FastAPI Depends: owner identity
# ---------------------------------------------------------------------------

async def get_owner_id(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Extract caller identity.

    Priority:
    1. Authorization: Bearer <supabase-jwt>   — Phase 2 production path
    2. X-User-ID: <uid>                        — Development / test fallback
                                                 (only allowed in development mode)
    """
    # --- JWT path (production) ---
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authorization header must use Bearer scheme")
        token = authorization[7:].strip()
        if settings.is_jwt_configured():
            return _verify_supabase_jwt(token, settings.supabase_jwt_secret)
        # JWT secret not configured: warn but allow in development
        if settings.is_development():
            logger.warning(
                "SUPABASE_JWT_SECRET not set; accepting token at face value (development only)"
            )
            # Decode without verification for dev convenience
            try:
                payload = jwt.decode(token, options={"verify_signature": False})
                sub = payload.get("sub", "dev-user")
                return sub
            except PyJWTError:
                raise HTTPException(status_code=401, detail="Cannot decode token")
        raise HTTPException(status_code=401, detail="SUPABASE_JWT_SECRET not configured")

    # --- X-User-ID header fallback (dev / tests only) ---
    if x_user_id and x_user_id.strip():
        if not settings.is_development():
            raise HTTPException(
                status_code=401,
                detail="X-User-ID header is only accepted in development mode",
            )
        return x_user_id.strip()

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide Authorization: Bearer <token>",
    )


