"""Tests for the artifact store.

FakeArtifactStore: full round-trip and path format.
SupabaseArtifactStore: unit tested with mocked supabase client.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.interfaces import FakeArtifactStore


# ---------------------------------------------------------------------------
# FakeArtifactStore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_store_round_trip():
    store = FakeArtifactStore()
    run_id = str(uuid.uuid4())
    data = b"PNG_BYTES_HERE"

    path = await store.store(run_id, "screenshot", data)
    assert path.startswith("fake/")
    assert run_id in path

    url = await store.get_signed_url(path)
    assert path in url
    assert "token=fake" in url


@pytest.mark.asyncio
async def test_fake_store_different_types():
    store = FakeArtifactStore()
    run_id = str(uuid.uuid4())

    p1 = await store.store(run_id, "screenshot", b"png")
    p2 = await store.store(run_id, "console_log", b"log")
    p3 = await store.store(run_id, "network_log", b"json")
    p4 = await store.store(run_id, "dom_snapshot", b"html")
    p5 = await store.store(run_id, "report", b"report")

    paths = [p1, p2, p3, p4, p5]
    assert len(set(paths)) == 5  # all unique


@pytest.mark.asyncio
async def test_fake_store_signed_url_expires():
    store = FakeArtifactStore()
    path = await store.store("run-1", "screenshot", b"x")
    url_1h = await store.get_signed_url(path, expires_in=3600)
    url_24h = await store.get_signed_url(path, expires_in=86400)
    assert "expires=3600" in url_1h
    assert "expires=86400" in url_24h


@pytest.mark.asyncio
async def test_fake_store_empty_data():
    store = FakeArtifactStore()
    path = await store.store("run-x", "report", b"")
    assert path is not None


@pytest.mark.asyncio
async def test_fake_store_metadata_accepted():
    store = FakeArtifactStore()
    path = await store.store(
        "run-1", "screenshot", b"data", metadata={"step_index": 3}
    )
    assert path is not None


# ---------------------------------------------------------------------------
# SupabaseArtifactStore — mocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supabase_artifact_store_upload_mocked(mocker):
    """SupabaseArtifactStore.store calls supabase storage upload."""
    mock_storage = mocker.MagicMock()
    mock_bucket = mocker.MagicMock()
    mock_storage.from_.return_value = mock_bucket
    mock_bucket.upload = mocker.MagicMock()
    mock_bucket.create_signed_url = mocker.MagicMock(
        return_value={"signedURL": "https://storage.example.com/signed?token=abc"}
    )

    # Patch create_client
    mock_client = mocker.MagicMock()
    mock_client.storage = mock_storage
    mock_client.storage.create_bucket = mocker.MagicMock()
    mocker.patch(
        "app.services.artifact_store.SupabaseArtifactStore.__init__",
        return_value=None,
    )

    from app.services.artifact_store import SupabaseArtifactStore
    store = SupabaseArtifactStore.__new__(SupabaseArtifactStore)
    store._client = mock_client
    store._bucket = "evidence-artifacts"
    store._ensured = True  # skip bucket creation

    path = await store.store("run-abc", "screenshot", b"PNG")
    assert "run-abc" in path
    assert path.endswith(".png")
    mock_bucket.upload.assert_called_once()


@pytest.mark.asyncio
async def test_supabase_artifact_store_signed_url_mocked(mocker):
    mock_storage = mocker.MagicMock()
    mock_bucket = mocker.MagicMock()
    mock_storage.from_.return_value = mock_bucket
    mock_bucket.create_signed_url = mocker.MagicMock(
        return_value={"signedURL": "https://storage.example.com/signed?token=xyz"}
    )

    mock_client = mocker.MagicMock()
    mock_client.storage = mock_storage

    from app.services.artifact_store import SupabaseArtifactStore
    store = SupabaseArtifactStore.__new__(SupabaseArtifactStore)
    store._client = mock_client
    store._bucket = "evidence-artifacts"
    store._ensured = True

    url = await store.get_signed_url("run-abc/screenshot_00aabbcc.png")
    assert "signed" in url
    mock_bucket.create_signed_url.assert_called_once()


# ---------------------------------------------------------------------------
# Path format validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_path_format_screenshot():
    store = FakeArtifactStore()
    run_id = "11111111-1111-1111-1111-111111111111"
    path = await store.store(run_id, "screenshot", b"x")
    # FakeArtifactStore uses "fake/{run_id}/screenshot" format
    assert run_id in path


@pytest.mark.asyncio
async def test_supabase_store_path_format(mocker):
    """Verify the storage path format: {run_id}/{type}_{uuid8}.{ext}"""
    import re
    from app.services.artifact_store import SupabaseArtifactStore

    mock_client = mocker.MagicMock()
    mock_client.storage.create_bucket = mocker.MagicMock()
    mock_bucket = mocker.MagicMock()
    mock_bucket.upload = mocker.MagicMock()
    mock_client.storage.from_.return_value = mock_bucket

    store = SupabaseArtifactStore.__new__(SupabaseArtifactStore)
    store._client = mock_client
    store._bucket = "evidence-artifacts"
    store._ensured = True

    run_id = str(uuid.uuid4())
    path = await store.store(run_id, "screenshot", b"data")

    # Expected: {run_id}/screenshot_{8hexchars}.png
    pattern = rf"^{re.escape(run_id)}/screenshot_[0-9a-f]{{8}}\.png$"
    assert re.match(pattern, path), f"Path {path!r} does not match expected format"
