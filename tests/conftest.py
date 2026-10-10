"""Shared test fixtures and helpers.

Phase 3 note:
  POST /tests/{id}/runs now drives the full orchestration lifecycle and returns
  a terminal-state run (passed/failed/needs_review/environment_failed), not a
  queued run.

  For tests that need a run in a *specific intermediate state* (e.g. transition
  tests that start from QUEUED), use the `queued_run_id` fixture which creates
  the run directly via the service, bypassing the orchestrator route.

  The `client` fixture injects FakeBrowserRunner(should_pass=True) and
  FakeSandboxRunner so the orchestrator always succeeds in tests. This keeps all
  Phase 1 API tests green while verifying the full lifecycle.
"""
import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    reset_execution_tools,
    reset_repository,
    set_browser_runner,
    set_repository,
    set_sandbox_runner,
    set_artifact_store,
)
from app.main import create_app
from app.persistence.in_memory import InMemoryRepository
from app.services.interfaces import FakeArtifactStore, FakeBrowserRunner, FakeSandboxRunner

# ---------------------------------------------------------------------------
# Common test data
# ---------------------------------------------------------------------------

OWNER_ID = "user-alice-001"
OTHER_OWNER_ID = "user-bob-002"
HEADERS = {"X-User-ID": OWNER_ID}
OTHER_HEADERS = {"X-User-ID": OTHER_OWNER_ID}

MINIMAL_ACTION_PLAN = {
    "steps": [
        {"action": "navigate", "target": "http://localhost:3000"},
        {"action": "click", "target": {"role": "button", "name": "Checkout"}},
        {"action": "assert_visible", "target": {"text": "Order confirmed"}},
    ],
    "required_assertions": [
        {"type": "visible", "target": {"text": "Order confirmed"}, "description": "Order confirmation shown"},
    ],
}

MINIMAL_TEST_BODY = {
    "name": "Checkout flow",
    "original_intent": "Complete checkout with valid payment and verify order confirmation is shown.",
    "required_assertions": [
        {"type": "visible", "target": {"text": "Order confirmed"}, "description": "Order confirmation shown"},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo():
    """Fresh isolated in-memory repository per test."""
    return InMemoryRepository()


@pytest.fixture
def client(repo):
    """FastAPI TestClient with an isolated, fresh repository and fake execution tools.

    Injects:
      - FakeSandboxRunner  → no E2B connection required
      - FakeBrowserRunner(should_pass=True) → orchestrator runs reach PASSED
      - FakeArtifactStore  → no Supabase Storage required

    This ensures POST /tests/{id}/runs drives the full lifecycle and returns
    a `passed` run, while keeping all API-level assertions valid.
    """
    app = create_app()
    set_repository(repo)
    set_sandbox_runner(FakeSandboxRunner())
    set_browser_runner(FakeBrowserRunner(should_pass=True))
    set_artifact_store(FakeArtifactStore())
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    reset_repository()
    reset_execution_tools()


@pytest.fixture
def project_id(client):
    """Create a project and return its ID."""
    r = client.post("/projects", json={"name": "Test Project"}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def test_id(client, project_id):
    """Create a test definition (no action plan yet) and return its ID."""
    r = client.post(
        f"/projects/{project_id}/tests",
        json=MINIMAL_TEST_BODY,
        headers=HEADERS,
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def test_id_with_version(client, project_id):
    """Create a test definition with an initial action plan version."""
    body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
    r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def run_id(client, test_id_with_version):
    """Create a run (via orchestrator) and return its ID.

    Phase 3: The run is now fully orchestrated and will be in a terminal state
    (passed, with FakeBrowserRunner). Tests that need to *read* a run record
    should use this fixture.

    For tests that need to *transition* a run from QUEUED, use `queued_run_id`.
    """
    r = client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.fixture
def queued_run_id(repo, project_id, test_id_with_version):
    """Create a run directly in QUEUED state via the service, bypassing the route.

    This fixture is for transition tests that need a run at the start of its
    lifecycle. It does NOT go through the orchestrator route.
    """
    import asyncio
    import uuid
    from app.models.domain import RunStatus, TestDefinition, TestRun
    from app.models.requests import CreateRunRequest

    async def _create():
        test_def = await repo.get_test_definition(uuid.UUID(test_id_with_version), OWNER_ID)
        run = TestRun(
            test_definition_id=test_def.id,
            test_version_id=test_def.active_version_id,
            owner_id=OWNER_ID,
            status=RunStatus.QUEUED,
        )
        return await repo.create_test_run(run)

    run = asyncio.run(_create())
    return str(run.id)
