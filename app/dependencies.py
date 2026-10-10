"""FastAPI dependency injection helpers.

The repository singleton is module-level so tests can override it
by calling set_repository() before creating the TestClient.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.persistence.in_memory import InMemoryRepository
from app.persistence.repository import PersistenceRepository
from app.services.test_definition_service import TestDefinitionService
from app.services.test_run_service import TestRunService

# ---------------------------------------------------------------------------
# Repository singleton (overridable for tests)
# ---------------------------------------------------------------------------

_repository: PersistenceRepository | None = None


def get_repository() -> PersistenceRepository:
    global _repository
    if _repository is None:
        _repository = InMemoryRepository()
    return _repository


def set_repository(repo: PersistenceRepository) -> None:
    """Override the repository singleton. Used by tests for isolation."""
    global _repository
    _repository = repo


def reset_repository() -> None:
    """Reset to default. Called between tests that need isolation."""
    global _repository
    _repository = None


# ---------------------------------------------------------------------------
# FastAPI Depends helpers
# ---------------------------------------------------------------------------

async def get_owner_id(x_user_id: str = Header(...)) -> str:
    """Extract caller identity from X-User-ID header.

    Phase 1: simple header-based identity.
    Phase 2: replaced with Supabase JWT verification.
    All ownership enforcement logic is already in place — only this function changes.
    """
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=401, detail="Missing or empty X-User-ID header")
    return x_user_id.strip()


async def get_test_definition_service(
    repo: PersistenceRepository = None,  # type: ignore[assignment]
) -> TestDefinitionService:
    if repo is None:
        repo = get_repository()
    return TestDefinitionService(repo)


async def get_test_run_service(
    repo: PersistenceRepository = None,  # type: ignore[assignment]
) -> TestRunService:
    if repo is None:
        repo = get_repository()
    return TestRunService(repo)
