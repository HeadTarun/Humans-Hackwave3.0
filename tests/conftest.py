"""Shared test fixtures and helpers."""
import pytest
from fastapi.testclient import TestClient

from app.dependencies import set_repository, reset_repository
from app.main import create_app
from app.persistence.in_memory import InMemoryRepository

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
    """FastAPI TestClient with an isolated, fresh repository."""
    app = create_app()
    set_repository(repo)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    reset_repository()


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
    """Create a queued run and return its ID."""
    r = client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["id"]
