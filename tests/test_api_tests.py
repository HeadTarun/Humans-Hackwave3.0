"""API tests for test definitions."""
import pytest
from tests.conftest import HEADERS, OTHER_HEADERS, MINIMAL_ACTION_PLAN, MINIMAL_TEST_BODY


class TestCreateTest:
    def test_create_minimal_returns_201(self, client, project_id):
        r = client.post(
            f"/projects/{project_id}/tests",
            json=MINIMAL_TEST_BODY,
            headers=HEADERS,
        )
        assert r.status_code == 201

    def test_response_shape(self, client, project_id):
        r = client.post(
            f"/projects/{project_id}/tests",
            json=MINIMAL_TEST_BODY,
            headers=HEADERS,
        )
        data = r.json()
        assert data["name"] == MINIMAL_TEST_BODY["name"]
        assert data["original_intent"] == MINIMAL_TEST_BODY["original_intent"]
        assert "id" in data
        assert "created_at" in data
        assert data["active_version_id"] is None  # No action plan supplied
        assert data["current_version"] is None

    def test_create_with_action_plan_creates_version_1(self, client, project_id):
        body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r.status_code == 201
        data = r.json()
        assert data["active_version_id"] is not None
        assert data["current_version"] is not None
        assert data["current_version"]["version_number"] == 1
        assert data["current_version"]["change_reason"] == "Initial version"

    def test_nonexistent_project_returns_404(self, client):
        r = client.post(
            "/projects/00000000-0000-0000-0000-000000000000/tests",
            json=MINIMAL_TEST_BODY,
            headers=HEADERS,
        )
        assert r.status_code == 404

    def test_other_owners_project_returns_404(self, client, project_id):
        r = client.post(
            f"/projects/{project_id}/tests",
            json=MINIMAL_TEST_BODY,
            headers=OTHER_HEADERS,  # Different user
        )
        assert r.status_code == 404

    def test_empty_intent_rejected(self, client, project_id):
        body = {**MINIMAL_TEST_BODY, "original_intent": ""}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r.status_code == 422

    def test_no_assertions_rejected(self, client, project_id):
        body = {**MINIMAL_TEST_BODY, "required_assertions": []}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r.status_code == 422

    def test_invalid_action_type_in_plan_rejected(self, client, project_id):
        bad_plan = {
            "steps": [{"action": "exec_shell", "target": "rm -rf /"}],
            "required_assertions": [{"type": "visible", "target": {"text": "x"}}],
        }
        body = {**MINIMAL_TEST_BODY, "action_plan": bad_plan}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r.status_code == 422


class TestGetTest:
    def test_get_returns_200(self, client, test_id):
        r = client.get(f"/tests/{test_id}", headers=HEADERS)
        assert r.status_code == 200

    def test_get_with_version(self, client, test_id_with_version):
        r = client.get(f"/tests/{test_id_with_version}", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["current_version"] is not None
        assert data["current_version"]["version_number"] == 1

    def test_get_nonexistent_returns_404(self, client):
        r = client.get("/tests/00000000-0000-0000-0000-000000000000", headers=HEADERS)
        assert r.status_code == 404

    def test_other_owner_cannot_access(self, client, test_id):
        r = client.get(f"/tests/{test_id}", headers=OTHER_HEADERS)
        assert r.status_code == 404
