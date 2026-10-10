"""API tests for the repository upload endpoint."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from tests.conftest import HEADERS, OTHER_HEADERS


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_nextjs_zip() -> bytes:
    pkg = json.dumps({"dependencies": {"next": "14.0.0", "react": "18.0.0"}}).encode()
    return make_zip({
        "package.json": pkg,
        "pages/index.js": b'export default function Home() { return <div>Hello</div>; }',
        "pages/api/checkout.js": b'export default async function handler(req, res) { res.json({ok:true}); }',
        "components/Button.jsx": b'export function Button() { return <button>Buy</button>; }',
    })


def upload_zip(client, project_id: str, zip_bytes: bytes, headers=None):
    headers = headers or HEADERS
    return client.post(
        f"/projects/{project_id}/repository",
        files={"file": ("repo.zip", zip_bytes, "application/zip")},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRepositoryUpload:
    def test_upload_returns_200(self, client, project_id):
        r = upload_zip(client, project_id, make_nextjs_zip())
        assert r.status_code == 200

    def test_response_has_repo_hash(self, client, project_id):
        r = upload_zip(client, project_id, make_nextjs_zip())
        data = r.json()
        assert "repo_hash" in data
        assert len(data["repo_hash"]) == 64  # SHA-256 hex

    def test_response_has_stack(self, client, project_id):
        r = upload_zip(client, project_id, make_nextjs_zip())
        data = r.json()
        assert data["stack"]["name"] == "nextjs"

    def test_response_has_inspection_summary(self, client, project_id):
        r = upload_zip(client, project_id, make_nextjs_zip())
        data = r.json()
        assert "inspection_summary" in data
        summary = data["inspection_summary"]
        assert "routes" in summary
        assert "api_handlers" in summary
        assert "relevant_files" in summary

    def test_same_content_produces_same_hash(self, client, project_id):
        z = make_nextjs_zip()
        r1 = upload_zip(client, project_id, z)
        r2 = upload_zip(client, project_id, z)
        assert r1.json()["repo_hash"] == r2.json()["repo_hash"]

    def test_different_content_produces_different_hash(self, client, project_id):
        z1 = make_zip({"package.json": b'{"dependencies":{"next":"14.0.0"}}', "a.js": b"v1"})
        z2 = make_zip({"package.json": b'{"dependencies":{"next":"14.0.0"}}', "a.js": b"v2"})
        r1 = upload_zip(client, project_id, z1)
        r2 = upload_zip(client, project_id, z2)
        assert r1.json()["repo_hash"] != r2.json()["repo_hash"]

    def test_repo_hash_stored_on_project(self, client, project_id):
        upload_zip(client, project_id, make_nextjs_zip())
        r = client.get(f"/projects/{project_id}", headers=HEADERS)
        assert r.status_code == 200
        meta = r.json().get("repo_metadata") or {}
        assert "repo_hash" in meta


# ---------------------------------------------------------------------------
# Security rejections
# ---------------------------------------------------------------------------

class TestRepositoryUploadSecurity:
    def test_rejects_path_traversal_zip(self, client, project_id):
        import struct
        # Build a ZIP with a path traversal entry name
        buf = io.BytesIO()
        info = zipfile.ZipInfo("../../evil.txt")
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(info, b"evil")
        r = upload_zip(client, project_id, buf.getvalue())
        assert r.status_code == 422
        assert "validation" in r.json()["detail"].lower() or "unsafe" in r.json()["detail"].lower()

    def test_rejects_blocked_env_file(self, client, project_id):
        z = make_zip({".env": b"SECRET=hunter2", "index.js": b"ok"})
        r = upload_zip(client, project_id, z)
        assert r.status_code == 422

    def test_rejects_invalid_zip(self, client, project_id):
        r = upload_zip(client, project_id, b"not a zip")
        assert r.status_code == 422

    def test_rejects_empty_file(self, client, project_id):
        r = upload_zip(client, project_id, b"")
        assert r.status_code == 422

    def test_other_owner_cannot_upload(self, client, project_id):
        r = upload_zip(client, project_id, make_nextjs_zip(), headers=OTHER_HEADERS)
        assert r.status_code == 404

    def test_nonexistent_project_returns_404(self, client):
        r = upload_zip(
            client,
            "00000000-0000-0000-0000-000000000000",
            make_nextjs_zip(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth (Phase 2: JWT)
# ---------------------------------------------------------------------------

class TestRepositoryAuth:
    def test_missing_auth_header_rejected(self, client, project_id):
        r = client.post(
            f"/projects/{project_id}/repository",
            files={"file": ("repo.zip", make_nextjs_zip(), "application/zip")},
            # No headers — no X-User-ID, no Authorization
        )
        assert r.status_code == 401

    def test_x_user_id_accepted_in_development(self, client, project_id):
        """X-User-ID header must still work (dev fallback)."""
        r = upload_zip(client, project_id, make_nextjs_zip(), headers=HEADERS)
        assert r.status_code == 200
