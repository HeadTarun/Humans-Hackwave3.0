"""Tests for deployment endpoints and production readiness:
- GET /projects (listing projects)
- GET /projects/{project_id}/tests (listing tests under a project)
- GET /runs/{run_id}/artifacts (listing artifacts with signed URLs)
- GET /runs/{run_id}/repair-proposals (listing proposals for a run)
- GET /repair-proposals/{proposal_id} (retrieving specific proposal)
- GET /health (enhanced health response)
- CORS preflight and headers verification
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import reset_execution_tools, reset_repository
from app.main import create_app
from app.models.domain import (
    ActionPlan,
    ActionStep,
    ActionTarget,
    ActionType,
    EvidenceArtifact,
    ArtifactType,
    Project,
    RepairProposal,
    RepairValidationStatus,
    RequiredAssertion,
    RunStatus,
    TestDefinition,
    TestRun,
    TestVersion,
)
from app.persistence.in_memory import InMemoryRepository


@pytest.fixture(autouse=True)
def clean_singletons():
    reset_repository()
    reset_execution_tools()
    yield
    reset_repository()
    reset_execution_tools()


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def client(repo):
    from app.dependencies import set_artifact_store, set_repository
    from app.services.interfaces import FakeArtifactStore
    set_repository(repo)
    set_artifact_store(FakeArtifactStore())
    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


USER_A = "user-alice"
USER_B = "user-bob"
AUTH_A = {"X-User-ID": USER_A}
AUTH_B = {"X-User-ID": USER_B}


# ---------------------------------------------------------------------------
# Health & CORS Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert "environment" in data
    assert "services" in data
    assert "supabase" in data["services"]
    assert "e2b" in data["services"]
    assert "groq" in data["services"]


@pytest.mark.asyncio
async def test_cors_headers(client: AsyncClient):
    # Preflight OPTIONS request
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "X-User-ID,Content-Type",
    }
    resp = await client.options("/projects", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in ("*", "http://localhost:3000")
    assert "POST" in resp.headers.get("access-control-allow-methods", "")

    # Standard GET with Origin header
    get_resp = await client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert get_resp.status_code == 200
    assert get_resp.headers.get("access-control-allow-origin") in ("*", "http://localhost:3000")


# ---------------------------------------------------------------------------
# Projects Listing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_empty(client: AsyncClient):
    resp = await client.get("/projects", headers=AUTH_A)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["projects"] == []


@pytest.mark.asyncio
async def test_list_projects_owner_isolation(client: AsyncClient, repo: InMemoryRepository):
    # Create projects for Alice
    await repo.create_project(Project(owner_id=USER_A, name="Alice Project 1"))
    await repo.create_project(Project(owner_id=USER_A, name="Alice Project 2"))

    # Create project for Bob
    await repo.create_project(Project(owner_id=USER_B, name="Bob Project"))

    # Alice should see 2 projects
    resp_a = await client.get("/projects", headers=AUTH_A)
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["total"] == 2
    assert {p["name"] for p in data_a["projects"]} == {"Alice Project 1", "Alice Project 2"}

    # Bob should see 1 project
    resp_b = await client.get("/projects", headers=AUTH_B)
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["total"] == 1
    assert data_b["projects"][0]["name"] == "Bob Project"


# ---------------------------------------------------------------------------
# Tests Listing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tests_under_project(client: AsyncClient, repo: InMemoryRepository):
    project = await repo.create_project(Project(owner_id=USER_A, name="My App"))

    # Create 2 tests
    td1 = await repo.create_test_definition(
        TestDefinition(
            project_id=project.id,
            owner_id=USER_A,
            name="Login Test",
            original_intent="Test login flow",
            required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="Welcome"))],
        )
    )
    td2 = await repo.create_test_definition(
        TestDefinition(
            project_id=project.id,
            owner_id=USER_A,
            name="Checkout Test",
            original_intent="Test checkout flow",
            required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="Confirmed"))],
        )
    )

    resp = await client.get(f"/projects/{project.id}/tests", headers=AUTH_A)
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == str(project.id)
    assert data["total"] == 2
    assert {t["name"] for t in data["tests"]} == {"Login Test", "Checkout Test"}


@pytest.mark.asyncio
async def test_list_tests_access_denied_for_other_user(client: AsyncClient, repo: InMemoryRepository):
    project = await repo.create_project(Project(owner_id=USER_A, name="Alice App"))

    # Bob tries to list Alice's tests
    resp = await client.get(f"/projects/{project.id}/tests", headers=AUTH_B)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Artifacts Listing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_run_artifacts(client: AsyncClient, repo: InMemoryRepository):
    project = await repo.create_project(Project(owner_id=USER_A, name="Test Project"))
    td = await repo.create_test_definition(
        TestDefinition(
            project_id=project.id,
            owner_id=USER_A,
            name="Test",
            original_intent="Intent",
            required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="OK"))],
        )
    )
    tv = await repo.create_test_version(
        TestVersion(
            test_definition_id=td.id,
            version_number=1,
            action_plan=ActionPlan(
                steps=[ActionStep(action=ActionType.NAVIGATE, target="http://example.com")],
                required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="OK"))],
            ),
            assertions_snapshot=td.required_assertions,
            created_by=USER_A,
            change_reason="Initial",
        )
    )
    run = await repo.create_test_run(
        TestRun(
            test_definition_id=td.id,
            test_version_id=tv.id,
            owner_id=USER_A,
            status=RunStatus.PASSED,
        )
    )

    # Add evidence artifacts
    art1 = await repo.create_evidence_artifact(
        EvidenceArtifact(
            test_run_id=run.id,
            artifact_type=ArtifactType.SCREENSHOT,
            storage_path=f"fake/{run.id}/screenshot.png",
        )
    )
    art2 = await repo.create_evidence_artifact(
        EvidenceArtifact(
            test_run_id=run.id,
            artifact_type=ArtifactType.DOM_SNAPSHOT,
            storage_path=f"fake/{run.id}/dom.html",
        )
    )

    resp = await client.get(f"/runs/{run.id}/artifacts", headers=AUTH_A)
    assert resp.status_code == 200
    data = resp.json()
    assert data["test_run_id"] == str(run.id)
    assert data["total"] == 2
    for item in data["artifacts"]:
        assert item["url"] is not None
        assert "token=fake" in item["url"]


# ---------------------------------------------------------------------------
# Repair Proposals Listing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_and_get_repair_proposals(client: AsyncClient, repo: InMemoryRepository):
    project = await repo.create_project(Project(owner_id=USER_A, name="Repair Project"))
    td = await repo.create_test_definition(
        TestDefinition(
            project_id=project.id,
            owner_id=USER_A,
            name="Repair Test",
            original_intent="Intent",
            required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="OK"))],
        )
    )
    tv = await repo.create_test_version(
        TestVersion(
            test_definition_id=td.id,
            version_number=1,
            action_plan=ActionPlan(
                steps=[ActionStep(action=ActionType.CLICK, target=ActionTarget(role="button", name="Old"))],
                required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="OK"))],
            ),
            assertions_snapshot=td.required_assertions,
            created_by=USER_A,
            change_reason="v1",
        )
    )
    run = await repo.create_test_run(
        TestRun(
            test_definition_id=td.id,
            test_version_id=tv.id,
            owner_id=USER_A,
            status=RunStatus.REPAIR_PROPOSED,
        )
    )

    prop = await repo.create_repair_proposal(
        RepairProposal(
            test_run_id=run.id,
            from_version_id=tv.id,
            proposed_plan=ActionPlan(
                steps=[ActionStep(action=ActionType.CLICK, target=ActionTarget(role="button", name="New"))],
                required_assertions=[RequiredAssertion(type="visible", target=ActionTarget(text="OK"))],
            ),
            reason="Locator updated from 'Old' to 'New'",
            evidence_refs=["fake/screenshot.png"],
            confidence=0.95,
        )
    )

    # 1. List proposals for run
    list_resp = await client.get(f"/runs/{run.id}/repair-proposals", headers=AUTH_A)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] == 1
    assert list_data["proposals"][0]["id"] == str(prop.id)
    assert list_data["proposals"][0]["confidence"] == 0.95

    # 2. Get specific proposal
    get_resp = await client.get(f"/repair-proposals/{prop.id}", headers=AUTH_A)
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["id"] == str(prop.id)
    assert get_data["reason"] == "Locator updated from 'Old' to 'New'"

    # 3. Access denied for Bob
    denied_resp = await client.get(f"/repair-proposals/{prop.id}", headers=AUTH_B)
    assert denied_resp.status_code in (403, 404)
