"""Tests for the sandbox runner — FakeSandboxRunner lifecycle and interface compliance.

No E2B key required for these tests. E2BSandboxRunner is unit-tested
with a fully mocked e2b library so no network calls are made.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.interfaces import FakeSandboxRunner, SandboxResult


# ---------------------------------------------------------------------------
# FakeSandboxRunner — lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_sandbox_create_returns_sandbox_result():
    runner = FakeSandboxRunner(base_url="http://localhost:3000")
    result = await runner.create_sandbox(repo_path="/tmp/repo", start_cmd="npm start")
    assert isinstance(result, SandboxResult)
    assert result.sandbox_id.startswith("fake-sandbox-")
    assert result.base_url == "http://localhost:3000"
    assert result.startup_time_ms >= 0
    assert result.startup_logs != ""


@pytest.mark.asyncio
async def test_fake_sandbox_sequential_ids():
    runner = FakeSandboxRunner()
    r1 = await runner.create_sandbox("/r1", "npm start")
    r2 = await runner.create_sandbox("/r2", "npm start")
    assert r1.sandbox_id != r2.sandbox_id
    assert len(runner.created) == 2


@pytest.mark.asyncio
async def test_fake_sandbox_destroy_tracked():
    runner = FakeSandboxRunner()
    result = await runner.create_sandbox("/repo", "npm start")
    await runner.destroy_sandbox(result.sandbox_id)
    assert result.sandbox_id in runner.destroyed


@pytest.mark.asyncio
async def test_fake_sandbox_idempotent_destroy():
    """Destroying the same sandbox twice should not raise."""
    runner = FakeSandboxRunner()
    result = await runner.create_sandbox("/repo", "npm start")
    await runner.destroy_sandbox(result.sandbox_id)
    await runner.destroy_sandbox(result.sandbox_id)  # second call — no error
    assert runner.destroyed.count(result.sandbox_id) == 2


@pytest.mark.asyncio
async def test_fake_sandbox_accepts_env_vars():
    runner = FakeSandboxRunner()
    result = await runner.create_sandbox(
        repo_path="/repo",
        start_cmd="npm start",
        env_vars={"NODE_ENV": "test", "PORT": "3000"},
    )
    assert result.sandbox_id is not None


# ---------------------------------------------------------------------------
# FakeSandboxRunner — state tracking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_sandbox_multiple_create_destroy():
    runner = FakeSandboxRunner()
    ids = []
    for _ in range(3):
        r = await runner.create_sandbox("/repo", "node app.js")
        ids.append(r.sandbox_id)

    for sid in ids:
        await runner.destroy_sandbox(sid)

    assert set(runner.created) == set(ids)
    assert set(runner.destroyed) == set(ids)


# ---------------------------------------------------------------------------
# E2BSandboxRunner — unit tests with mocked e2b
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2b_sandbox_runner_create_destroy_mocked():
    """E2BSandboxRunner create/destroy with fully mocked e2b.Sandbox."""
    e2b_mock = MagicMock()
    sandbox_instance = MagicMock()
    sandbox_instance.sandbox_id = "e2b-sandbox-abc123"
    sandbox_instance.get_hostname.return_value = "abc123.e2b.dev"
    sandbox_instance.files.write_bytes = MagicMock()
    sandbox_instance.process.start_and_wait = MagicMock(return_value=MagicMock(stdout="installed"))
    sandbox_instance.process.start = MagicMock()
    sandbox_instance.close = MagicMock()
    e2b_mock.Sandbox.return_value = sandbox_instance
    e2b_mock.Sandbox.kill = MagicMock()

    import importlib
    import sys

    # Inject mock e2b module
    sys.modules["e2b"] = e2b_mock

    try:
        from app.services.sandbox_runner import E2BSandboxRunner
        runner = E2BSandboxRunner(api_key="fake-key", timeout=60)

        # Patch the health check so we don't actually poll HTTP
        with patch.object(runner, "_wait_for_health", new_callable=AsyncMock):
            result = await runner.create_sandbox(
                repo_path=str(__file__),  # just needs to be a valid path
                start_cmd="npm start",
            )

        assert result.sandbox_id == "e2b-sandbox-abc123"
        assert "abc123.e2b.dev" in result.base_url

    finally:
        # Clean up injected mock
        if "e2b" in sys.modules:
            del sys.modules["e2b"]
        # Remove cached module if loaded
        for key in list(sys.modules.keys()):
            if "sandbox_runner" in key:
                del sys.modules[key]


@pytest.mark.asyncio
async def test_e2b_sandbox_runner_destroy_does_not_raise_on_error():
    """destroy_sandbox must be safe to call even if E2B errors."""
    import sys
    e2b_mock = MagicMock()
    e2b_mock.Sandbox.kill.side_effect = RuntimeError("connection refused")
    sys.modules["e2b"] = e2b_mock

    try:
        from app.services.sandbox_runner import E2BSandboxRunner
        runner = E2BSandboxRunner(api_key="key")
        # Should not raise
        await runner.destroy_sandbox("some-sandbox-id")
    finally:
        del sys.modules["e2b"]
        for key in list(sys.modules.keys()):
            if "sandbox_runner" in key:
                del sys.modules[key]


# ---------------------------------------------------------------------------
# SandboxResult dataclass
# ---------------------------------------------------------------------------

def test_sandbox_result_dataclass():
    r = SandboxResult(
        sandbox_id="sbx-001",
        base_url="https://sbx-001.e2b.dev",
        startup_logs="started on port 3000",
        startup_time_ms=4200,
        repo_path="/home/user/repo",
    )
    assert r.sandbox_id == "sbx-001"
    assert r.startup_time_ms == 4200
