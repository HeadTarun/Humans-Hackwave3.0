"""Tests for Phase 2 authentication — JWT verification and X-User-ID dev fallback."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.dependencies import get_owner_id, reset_repository, set_repository
from app.main import create_app
from app.persistence.in_memory import InMemoryRepository


JWT_SECRET = "test-secret-for-unit-tests-32chars!!"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_client(repo):
    """TestClient with JWT secret configured in settings."""
    import os
    os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
    os.environ["APP_ENV"] = "development"

    # Reset lru_cache so settings picks up env vars
    get_settings.cache_clear()

    app = create_app()
    set_repository(repo)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    # Cleanup
    del os.environ["SUPABASE_JWT_SECRET"]
    get_settings.cache_clear()
    reset_repository()


def make_jwt(sub: str = "user-jwt-001", secret: str = JWT_SECRET) -> str:
    """Create a valid Supabase-style HS256 JWT."""
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def make_expired_jwt(sub: str = "user-expired") -> str:
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "exp": int(time.time()) - 3600,  # already expired
        "iat": int(time.time()) - 7200,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# X-User-ID fallback (dev mode)
# ---------------------------------------------------------------------------

class TestDevFallback:
    def test_x_user_id_accepted_in_dev(self, client):
        """Phase 1 header still works in development mode."""
        r = client.post("/projects", json={"name": "P"}, headers={"X-User-ID": "alice"})
        assert r.status_code == 201
        assert r.json()["owner_id"] == "alice"

    def test_missing_auth_returns_401(self, client):
        r = client.post("/projects", json={"name": "P"})
        assert r.status_code == 401

    def test_empty_x_user_id_returns_401(self, client):
        r = client.post("/projects", json={"name": "P"}, headers={"X-User-ID": "  "})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------

class TestJWTAuth:
    def test_valid_jwt_authenticates(self, auth_client):
        token = make_jwt("user-jwt-001")
        r = auth_client.post(
            "/projects",
            json={"name": "JWT Project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["owner_id"] == "user-jwt-001"

    def test_expired_jwt_rejected(self, auth_client):
        token = make_expired_jwt()
        r = auth_client.post(
            "/projects",
            json={"name": "P"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_wrong_secret_rejected(self, auth_client):
        token = make_jwt("user-x", secret="completely-wrong-secret-that-is-invalid!!")
        r = auth_client.post(
            "/projects",
            json={"name": "P"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    def test_malformed_token_rejected(self, auth_client):
        r = auth_client.post(
            "/projects",
            json={"name": "P"},
            headers={"Authorization": "Bearer not.a.jwt"},
        )
        assert r.status_code == 401

    def test_missing_bearer_scheme_rejected(self, auth_client):
        token = make_jwt()
        r = auth_client.post(
            "/projects",
            json={"name": "P"},
            headers={"Authorization": token},  # Missing "Bearer " prefix
        )
        assert r.status_code == 401

    def test_jwt_owner_isolation(self, auth_client):
        """Two JWT users cannot access each other's resources."""
        alice_token = make_jwt("alice")
        bob_token = make_jwt("bob")

        # Alice creates a project
        r = auth_client.post(
            "/projects",
            json={"name": "Alice project"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert r.status_code == 201
        project_id = r.json()["id"]

        # Bob cannot see it
        r = auth_client.get(
            f"/projects/{project_id}",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert r.status_code == 404
