"""E2B Sandbox Runner — Phase 3.

Creates disposable E2B sandboxes, uploads repo files, starts the app,
waits for the HTTP port to be ready, and returns a public base URL.

Design decisions:
- File upload: e2b sandbox.files.write_bytes() per file
- Dependency install: detected from stack (npm / pip)
- Port wait: exponential backoff polling localhost:3000 inside sandbox
- Cleanup: destroy_sandbox() always called in finally blocks
- Resource limits: timeout controlled by settings.e2b_timeout_seconds
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    """Outcome of a successful sandbox creation."""
    sandbox_id: str
    base_url: str           # Public E2B URL reachable from internet
    startup_logs: str       # Combined stdout/stderr from start_cmd
    startup_time_ms: int    # Wall-clock ms from create→healthy
    repo_path: str          # Path inside sandbox where repo was written


# ---------------------------------------------------------------------------
# Abstract base (imported from interfaces, kept here for clarity)
# ---------------------------------------------------------------------------

from app.services.interfaces import SandboxRunner  # noqa: E402


# ---------------------------------------------------------------------------
# E2B real implementation
# ---------------------------------------------------------------------------

class E2BSandboxRunner(SandboxRunner):
    """Creates disposable E2B sandboxes for running uploaded repos.

    Requires E2B_API_KEY in environment / settings.
    """

    SANDBOX_REPO_PATH = "/home/user/repo"
    PORT = 3000
    HEALTH_POLL_INTERVAL = 2   # seconds between health checks
    HEALTH_MAX_WAIT = 60       # seconds before giving up
    HEALTH_URL_PATH = "/"      # path to check for 200

    def __init__(self, api_key: str, timeout: int = 300) -> None:
        self._api_key = api_key
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API (overrides abstract interface)
    # ------------------------------------------------------------------

    async def create_sandbox(
        self,
        repo_path: str,
        start_cmd: str,
        env_vars: dict[str, str] | None = None,
    ) -> SandboxResult:  # type: ignore[override]
        """
        Full sandbox lifecycle:
        1. Create E2B sandbox
        2. Upload repo files
        3. Install dependencies
        4. Start app with start_cmd
        5. Wait for HTTP health check on port 3000
        6. Return SandboxResult
        """
        from e2b import Sandbox  # type: ignore[import]

        t0 = time.monotonic()
        sandbox = await asyncio.to_thread(
            Sandbox,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        sandbox_id = sandbox.sandbox_id
        logger.info("E2B sandbox created: %s", sandbox_id)

        try:
            # 2. Upload repo files
            await self._upload_repo(sandbox, repo_path)

            # 3. Install dependencies
            install_logs = await self._install_deps(sandbox, repo_path)
            logger.info("Deps installed in sandbox %s", sandbox_id)

            # 4. Start app (background process)
            cmd = f"cd {self.SANDBOX_REPO_PATH} && {start_cmd}"
            process = await asyncio.to_thread(
                sandbox.process.start,
                cmd,
                env_vars=env_vars or {},
                on_stdout=lambda x: logger.debug("[sandbox stdout] %s", x.line),
                on_stderr=lambda x: logger.debug("[sandbox stderr] %s", x.line),
            )

            # 5. Get public base URL
            host = sandbox.get_hostname(self.PORT)
            base_url = f"https://{host}"

            # 6. Wait for health
            await self._wait_for_health(sandbox, base_url)

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "Sandbox %s ready at %s in %dms",
                sandbox_id, base_url, elapsed_ms,
            )
            return SandboxResult(
                sandbox_id=sandbox_id,
                base_url=base_url,
                startup_logs=install_logs,
                startup_time_ms=elapsed_ms,
                repo_path=self.SANDBOX_REPO_PATH,
            )

        except Exception:
            # If setup fails, destroy sandbox to avoid leaks
            try:
                await asyncio.to_thread(sandbox.close)
            except Exception:
                pass
            raise

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        """Destroy sandbox. Safe to call multiple times (idempotent)."""
        try:
            from e2b import Sandbox  # type: ignore[import]
            await asyncio.to_thread(Sandbox.kill, sandbox_id, api_key=self._api_key)
            logger.info("E2B sandbox destroyed: %s", sandbox_id)
        except Exception as exc:
            # Log but never raise — destroy is called in finally blocks
            logger.warning("Failed to destroy sandbox %s: %s", sandbox_id, exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _upload_repo(self, sandbox: object, repo_path: str) -> None:
        """Upload all files from local repo_path to sandbox filesystem."""
        src = Path(repo_path)
        files_uploaded = 0
        for file_path in src.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(src)
                dest = f"{self.SANDBOX_REPO_PATH}/{rel.as_posix()}"
                data = file_path.read_bytes()
                await asyncio.to_thread(sandbox.files.write_bytes, dest, data)
                files_uploaded += 1
        logger.info("Uploaded %d files to sandbox", files_uploaded)

    async def _install_deps(self, sandbox: object, repo_path: str) -> str:
        """Detect stack and install dependencies. Returns combined output."""
        src = Path(repo_path)
        logs: list[str] = []

        if (src / "package.json").exists():
            result = await asyncio.to_thread(
                sandbox.process.start_and_wait,
                f"cd {self.SANDBOX_REPO_PATH} && npm install --prefer-offline 2>&1",
            )
            logs.append(result.stdout or "")
        elif (src / "requirements.txt").exists():
            result = await asyncio.to_thread(
                sandbox.process.start_and_wait,
                f"cd {self.SANDBOX_REPO_PATH} && pip install -r requirements.txt -q 2>&1",
            )
            logs.append(result.stdout or "")
        else:
            logs.append("No package.json or requirements.txt found; skipping dep install.")

        return "\n".join(logs)

    async def _wait_for_health(self, sandbox: object, base_url: str) -> None:
        """Poll base_url until 200 OK or timeout. Raises EnvironmentError on timeout."""
        import httpx

        deadline = time.monotonic() + self.HEALTH_MAX_WAIT
        interval = self.HEALTH_POLL_INTERVAL

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(base_url + self.HEALTH_URL_PATH)
                    if resp.status_code < 500:
                        return
            except Exception:
                pass
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 10)  # cap at 10s

        raise EnvironmentError(
            f"App did not become healthy at {base_url} within {self.HEALTH_MAX_WAIT}s"
        )
