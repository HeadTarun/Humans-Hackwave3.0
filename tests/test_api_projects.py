"""API tests for projects."""
import pytest
from tests.conftest import HEADERS, OTHER_HEADERS, OWNER_ID


class TestCreateProject:
    def test_create_returns_201(self, client):
        r = client.post("/projects", json={"name": "My Project"}, headers=HEADERS)
        assert r.status_code == 201

    def test_response_shape(self, client):
        r = client.post("/projects", json={"name": "Alpha"}, headers=HEADERS)
        data = r.json()
        assert data["name"] == "Alpha"
        assert data["owner_id"] == OWNER_ID
        assert "id" in data
        assert "created_at" in data

    def test_missing_name_rejected(self, client):
        r = client.post("/projects", json={}, headers=HEADERS)
        assert r.status_code == 422

    def test_empty_name_rejected(self, client):
        r = client.post("/projects", json={"name": ""}, headers=HEADERS)
        assert r.status_code == 422

    def test_missing_user_id_header_rejected(self, client):
        r = client.post("/projects", json={"name": "X"})
        # Phase 2: explicit auth check returns 401, not Pydantic 422
        assert r.status_code == 401

    def test_repo_metadata_stored(self, client):
        r = client.post(
            "/projects",
            json={"name": "P", "repo_metadata": {"lang": "python"}},
            headers=HEADERS,
        )
        assert r.status_code == 201
        assert r.json()["repo_metadata"] == {"lang": "python"}


class TestGetProject:
    def test_get_existing_project(self, client, project_id):
        r = client.get(f"/projects/{project_id}", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["id"] == project_id

    def test_get_nonexistent_returns_404(self, client):
        r = client.get("/projects/00000000-0000-0000-0000-000000000000", headers=HEADERS)
        assert r.status_code == 404

    def test_other_owner_cannot_access(self, client, project_id):
        r = client.get(f"/projects/{project_id}", headers=OTHER_HEADERS)
        assert r.status_code == 404  # Not 403 — don't reveal existence
