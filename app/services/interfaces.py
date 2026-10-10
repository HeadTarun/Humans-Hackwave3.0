"""Dependency interfaces for Phase 2+ integrations.

These ABCs define the contracts that real tool integrations must satisfy.
Fake implementations below are used by Phase 1 tests.

Integration schedule:
  RepositoryInspector  → Tree-sitter   (Phase 2)
  SandboxRunner        → E2B           (Phase 3)
  BrowserRunner        → Playwright    (Phase 3)
  FailureClassifier    → Rules + Groq  (Phase 4/5)
  ArtifactStore        → Supabase Storage (Phase 3)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.models.domain import ActionPlan, FailureCategory


@dataclass
class ClassificationResult:
    category: FailureCategory
    confidence: float  # 0.0 – 1.0
    summary: str
    requires_human_review: bool = False
    evidence_used: list[str] = field(default_factory=list)
    repair_candidate: dict[str, Any] | None = None  # Validated JSON only


@dataclass
class SandboxResult:
    """Result of a successful sandbox creation (Phase 3)."""
    sandbox_id: str
    base_url: str           # Public URL reachable by Playwright
    startup_logs: str       # Combined stdout/stderr from start_cmd
    startup_time_ms: int    # Wall-clock ms from create → healthy
    repo_path: str          # Path inside sandbox where repo was written


# ---------------------------------------------------------------------------
# Abstract interfaces
# ---------------------------------------------------------------------------

class RepositoryInspector(ABC):
    """Phase 2: Tree-sitter — extracts routes, components, handlers."""

    @abstractmethod
    async def inspect(self, repo_path: str) -> dict[str, Any]:
        """Return a bounded, redacted code-context bundle. Never executes code."""
        ...


class SandboxRunner(ABC):
    """Phase 3: E2B — creates disposable isolated sandboxes."""

    @abstractmethod
    async def create_sandbox(
        self,
        repo_path: str,
        start_cmd: str,
        env_vars: dict[str, str] | None = None,
    ) -> "SandboxResult":
        """Create sandbox, upload repo, start app, return SandboxResult."""
        ...

    @abstractmethod
    async def destroy_sandbox(self, sandbox_id: str) -> None:
        """Tear down sandbox unconditionally (called in finally blocks)."""
        ...


class BrowserRunner(ABC):
    """Phase 3: Playwright — executes structured action plans, captures evidence."""

    @abstractmethod
    async def execute_plan(
        self, base_url: str, plan: ActionPlan
    ) -> "Any":
        """Run plan, return BrowserEvidence (or dict for fakes)."""
        ...


class FailureClassifier(ABC):
    """Phase 4: deterministic rules; Phase 5: Groq fallback for ambiguous cases."""

    @abstractmethod
    async def classify(
        self,
        evidence: dict[str, Any],
        repo_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """Classify failure.

        Args:
            evidence:     Serialised BrowserEvidence dict (from to_dict()).
            repo_context: Optional Tree-sitter inspection bundle stored in
                          project.repo_metadata["inspection_result"].
                          Provides source-confirmed locator candidates.

        Rules:
            Deterministic rules first; Groq only when rules cannot decide.
        """
        ...


class ArtifactStore(ABC):
    """Supabase private Storage. Provides signed URLs for retrieval."""

    @abstractmethod
    async def store(
        self, run_id: str, artifact_type: str, data: bytes, metadata: dict | None = None
    ) -> str:
        """Store artifact, return private storage_path (not a public URL)."""
        ...

    @abstractmethod
    async def get_signed_url(self, storage_path: str, expires_in: int = 3600) -> str:
        """Return a time-limited signed URL for a private artifact."""
        ...


# ---------------------------------------------------------------------------
# Fake implementations for Phase 1 tests
# ---------------------------------------------------------------------------

class FakeRepositoryInspector(RepositoryInspector):
    """Returns a minimal code context bundle. Used in tests."""

    async def inspect(self, repo_path: str) -> dict[str, Any]:
        return {
            "stack": "unknown",
            "routes": [],
            "components": [],
            "repo_path": repo_path,
        }


class FakeSandboxRunner(SandboxRunner):
    """Returns a fake SandboxResult immediately. No E2B connection."""

    def __init__(self, base_url: str = "http://localhost:3000") -> None:
        self.base_url = base_url
        self.created: list[str] = []
        self.destroyed: list[str] = []

    async def create_sandbox(
        self,
        repo_path: str,
        start_cmd: str,
        env_vars: dict[str, str] | None = None,
    ) -> SandboxResult:
        sandbox_id = f"fake-sandbox-{len(self.created) + 1}"
        self.created.append(sandbox_id)
        return SandboxResult(
            sandbox_id=sandbox_id,
            base_url=self.base_url,
            startup_logs="[fake] app started",
            startup_time_ms=0,
            repo_path="/fake/repo",
        )

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)



class FakeBrowserRunner(BrowserRunner):
    """Returns configurable fake evidence. Used to test lifecycle without Playwright."""

    def __init__(
        self,
        should_pass: bool = True,
        extra_evidence: dict[str, Any] | None = None,
    ) -> None:
        self.should_pass = should_pass
        self.extra_evidence = extra_evidence or {}
        self.calls: list[dict] = []

    async def execute_plan(
        self, base_url: str, plan: ActionPlan
    ) -> dict[str, Any]:
        call = {"base_url": base_url, "steps": len(plan.steps)}
        self.calls.append(call)
        evidence: dict[str, Any] = {
            "passed": self.should_pass,
            "assertions_passed": self.should_pass,
            "console_errors": [],
            "network_failures": [],
            "screenshot_path": "fake/screenshot.png",
        }
        evidence.update(self.extra_evidence)
        return evidence


class FakeFailureClassifier(FailureClassifier):
    """Returns a configurable classification. Used in tests."""

    def __init__(self, result: ClassificationResult | None = None) -> None:
        self._result = result or ClassificationResult(
            category=FailureCategory.LOCATOR_DRIFT,
            confidence=0.85,
            summary="Fake: locator drift detected",
        )

    async def classify(
        self,
        evidence: dict[str, Any],
        repo_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        return self._result


class FakeArtifactStore(ArtifactStore):
    """Stores artifacts in memory. No Supabase Storage connection."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def store(
        self, run_id: str, artifact_type: str, data: bytes, metadata: dict | None = None
    ) -> str:
        path = f"fake/{run_id}/{artifact_type}"
        self._store[path] = data
        return path

    async def get_signed_url(self, storage_path: str, expires_in: int = 3600) -> str:
        return f"http://fake-storage/{storage_path}?token=fake&expires={expires_in}"
