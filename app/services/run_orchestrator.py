"""Run Orchestrator — Phase 3.

Drives a test run through its full lifecycle without manual /transition calls:
  queued → preparing → running → collecting_evidence → classifying → verdict

The orchestrator is the single entry point for automated execution.
It owns the sandbox lifecycle (always destroys in finally), stores evidence
artifacts, and transitions the run through every state.

Phase 4 plugs real Groq classification in via the FailureClassifier interface.
Phase 3 ships a DeterministicClassifier that uses rule-based logic only.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.models.domain import (
    ArtifactType,
    EvidenceArtifact,
    FailureCategory,
    RunStatus,
    TestRun,
    is_terminal_status,
    is_valid_transition,
    VALID_TRANSITIONS,
)
from app.models.requests import TransitionRunRequest
from app.persistence.repository import PersistenceRepository
from app.services.interfaces import (
    ArtifactStore,
    BrowserRunner,
    ClassificationResult,
    FailureClassifier,
    SandboxRunner,
)
from app.services.test_run_service import TestRunService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic classifier (Phase 3 stub; Phase 4 adds Groq)
# ---------------------------------------------------------------------------

class DeterministicClassifier(FailureClassifier):
    """Rule-based failure classifier that doesn't require an LLM.

    Rules (in priority order):
    1. Network failures with 5xx on checkout/api paths → API_FAILURE
    2. Locator errors in step failure → LOCATOR_DRIFT
    3. Assertion failures → ASSERTION_FAILURE
    4. General step failures → UI_FUNCTIONAL_REGRESSION
    5. Everything else → AMBIGUOUS_REQUIRES_REVIEW
    """

    async def classify(self, evidence: dict[str, Any]) -> ClassificationResult:
        network_failures = evidence.get("network_failures", [])
        failed_error = evidence.get("failed_step_error") or ""
        steps_failed = evidence.get("steps_failed", 0)
        assertion_results = evidence.get("assertion_results", [])

        # Rule 1: 5xx API failure
        api_failures = [
            f for f in network_failures if f.get("status", 0) >= 500
        ]
        if api_failures:
            urls = [f["url"] for f in api_failures]
            return ClassificationResult(
                category=FailureCategory.API_FAILURE,
                confidence=0.92,
                summary=f"API returned 5xx on: {', '.join(urls[:3])}",
                requires_human_review=False,
            )

        # Rule 2: Locator drift (playwright TimeoutError on locator resolution)
        locator_keywords = ("timeout", "locator", "get_by_role", "get_by_text", "waitfor")
        if steps_failed > 0 and any(kw in failed_error.lower() for kw in locator_keywords):
            return ClassificationResult(
                category=FailureCategory.LOCATOR_DRIFT,
                confidence=0.80,
                summary=f"Locator resolution failed: {failed_error[:200]}",
                requires_human_review=False,
            )

        # Rule 3: Assertion failure
        failed_assertions = [r for r in assertion_results if not r.get("passed")]
        if failed_assertions:
            detail = failed_assertions[0].get("detail", "")
            return ClassificationResult(
                category=FailureCategory.ASSERTION_FAILURE,
                confidence=0.88,
                summary=f"Required assertion failed: {detail[:200]}",
                requires_human_review=False,
            )

        # Rule 4: General step failure
        if steps_failed > 0:
            return ClassificationResult(
                category=FailureCategory.UI_FUNCTIONAL_REGRESSION,
                confidence=0.65,
                summary=f"Step execution failed: {failed_error[:200]}",
                requires_human_review=True,
            )

        # Rule 5: Ambiguous
        return ClassificationResult(
            category=FailureCategory.AMBIGUOUS_REQUIRES_REVIEW,
            confidence=0.40,
            summary="Could not classify failure with available evidence",
            requires_human_review=True,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class RunOrchestrator:
    """Drives a TestRun through its full automated lifecycle.

    Callers:
        run = await orchestrator.execute_run(test_id, owner_id, project)

    The run is always returned — even if it ends in environment_failed.
    """

    def __init__(
        self,
        repo: PersistenceRepository,
        sandbox: SandboxRunner,
        browser: BrowserRunner,
        classifier: FailureClassifier,
        artifact_store: ArtifactStore,
    ) -> None:
        self._repo = repo
        self._sandbox = sandbox
        self._browser = browser
        self._classifier = classifier
        self._artifact_store = artifact_store
        self._run_svc = TestRunService(repo)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute_run(
        self,
        test_id: uuid.UUID,
        owner_id: str,
        environment_ref: Optional[str] = None,
        start_command: Optional[str] = None,
    ) -> TestRun:
        """Full lifecycle: create → prepare → run → collect → classify → verdict.

        Always destroys the sandbox in a finally block.
        Always returns the final TestRun (never raises on business failures).
        """
        from app.models.requests import CreateRunRequest

        # Verify test + active version exist
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )
        if not test_def.active_version_id:
            raise HTTPException(
                status_code=409,
                detail="Test definition has no active version. Create a version first.",
            )

        # Fetch project for start_command
        from app.models.domain import Project
        # We need the project to get start_command
        project = await self._get_project_for_test(test_def, owner_id)

        # Create the run in QUEUED state
        run = await self._run_svc.create_run(
            test_id,
            owner_id,
            CreateRunRequest(environment_ref=environment_ref),
        )

        sandbox_id: Optional[str] = None

        try:
            # ── PREPARING ──────────────────────────────────────────────
            run = await self._transition(run, RunStatus.PREPARING, owner_id)

            start_cmd = start_command or self._resolve_start_command(project)
            repo_path = self._resolve_repo_path(project)

            try:
                sandbox_result = await self._sandbox.create_sandbox(
                    repo_path=repo_path,
                    start_cmd=start_cmd,
                )
                sandbox_id = sandbox_result.sandbox_id
                base_url = sandbox_result.base_url
            except Exception as exc:
                logger.error("Sandbox creation failed: %s", exc)
                run = await self._fail_environment(
                    run, owner_id, f"Sandbox setup failed: {exc}"
                )
                return run

            # ── RUNNING ────────────────────────────────────────────────
            run = await self._transition(run, RunStatus.RUNNING, owner_id)

            version = await self._repo.get_test_version(test_def.active_version_id)
            if not version:
                run = await self._fail_environment(
                    run, owner_id, "Active test version not found"
                )
                return run

            try:
                raw_evidence = await self._browser.execute_plan(
                    base_url, version.action_plan
                )
            except Exception as exc:
                logger.error("Browser execution failed: %s", exc)
                run = await self._fail_environment(
                    run, owner_id, f"Browser execution error: {exc}"
                )
                return run

            # ── COLLECTING_EVIDENCE ────────────────────────────────────
            run = await self._transition(run, RunStatus.COLLECTING_EVIDENCE, owner_id)

            evidence_dict = await self._store_evidence(run, raw_evidence)

            # ── CLASSIFYING ────────────────────────────────────────────
            run = await self._transition(run, RunStatus.CLASSIFYING, owner_id)

            # Determine verdict
            # BrowserEvidence or dict from FakeBrowserRunner
            if hasattr(raw_evidence, "passed"):
                passed = raw_evidence.passed
                ev_dict = raw_evidence.to_dict() if hasattr(raw_evidence, "to_dict") else evidence_dict
            else:
                passed = raw_evidence.get("passed", False)
                ev_dict = raw_evidence

            if passed:
                run = await self._transition(
                    run, RunStatus.PASSED, owner_id,
                    classification=FailureCategory.ASSERTION_FAILURE,  # unused for PASSED
                    confidence=1.0,
                    summary="All steps and assertions passed",
                    # Don't set classification on PASSED — optional
                    skip_classification=True,
                )
            else:
                classification = await self._classifier.classify(ev_dict)
                if classification.requires_human_review:
                    target_status = RunStatus.NEEDS_REVIEW
                else:
                    target_status = RunStatus.FAILED

                run = await self._transition(
                    run, target_status, owner_id,
                    classification=classification.category,
                    confidence=classification.confidence,
                    summary=classification.summary,
                )

            return run

        finally:
            if sandbox_id:
                await self._sandbox.destroy_sandbox(sandbox_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _transition(
        self,
        run: TestRun,
        to_status: RunStatus,
        owner_id: str,
        classification: Optional[FailureCategory] = None,
        confidence: Optional[float] = None,
        summary: Optional[str] = None,
        skip_classification: bool = False,
    ) -> TestRun:
        """Transition run to new status, respecting the state machine."""
        req = TransitionRunRequest(
            status=to_status,
            classification=classification if not skip_classification else None,
            confidence=confidence if not skip_classification else None,
            summary=summary,
        )
        updated = await self._run_svc.transition(run.id, owner_id, req)
        logger.info(
            "Run %s: %s → %s", run.id, run.status.value, to_status.value
        )
        return updated

    async def _fail_environment(
        self, run: TestRun, owner_id: str, reason: str
    ) -> TestRun:
        """Transition run to ENVIRONMENT_FAILED from whatever state it's currently in."""
        # Walk the state machine to ENVIRONMENT_FAILED if possible
        current = run.status
        if is_valid_transition(current, RunStatus.ENVIRONMENT_FAILED):
            return await self._transition(
                run, RunStatus.ENVIRONMENT_FAILED, owner_id,
                summary=reason,
            )
        # Already terminal or no valid path — just return as-is
        logger.warning(
            "Cannot transition %s to ENVIRONMENT_FAILED from %s",
            run.id, current.value,
        )
        return run

    async def _store_evidence(
        self, run: TestRun, evidence: Any
    ) -> dict[str, Any]:
        """Store evidence artifacts and create EvidenceArtifact DB records."""
        run_id = str(run.id)

        # Handle both BrowserEvidence dataclass and plain dict (FakeBrowserRunner)
        if hasattr(evidence, "to_dict"):
            ev_dict = evidence.to_dict()
            screenshots = evidence.screenshots
            console_errors = evidence.console_errors
            network_failures = evidence.network_failures
            dom_snapshot = evidence.dom_snapshot
        else:
            ev_dict = evidence
            screenshots = []
            console_errors = evidence.get("console_errors", [])
            network_failures = evidence.get("network_failures", [])
            dom_snapshot = None

        stored_paths: list[str] = []

        # Screenshots (one per step)
        for i, shot_bytes in enumerate(screenshots):
            path = await self._artifact_store.store(
                run_id, "screenshot", shot_bytes,
                metadata={"step_index": i}
            )
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.SCREENSHOT,
                storage_path=path,
                metadata={"step_index": i},
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Console log
        if console_errors:
            log_bytes = "\n".join(console_errors).encode("utf-8")
            path = await self._artifact_store.store(run_id, "console_log", log_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.CONSOLE_LOG,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Network log
        if network_failures:
            net_bytes = json.dumps(network_failures, indent=2).encode("utf-8")
            path = await self._artifact_store.store(run_id, "network_log", net_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.NETWORK_LOG,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # DOM snapshot
        if dom_snapshot:
            dom_bytes = dom_snapshot.encode("utf-8")
            path = await self._artifact_store.store(run_id, "dom_snapshot", dom_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.DOM_SNAPSHOT,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Report (full evidence dict)
        report_bytes = json.dumps(ev_dict, indent=2, default=str).encode("utf-8")
        path = await self._artifact_store.store(run_id, "report", report_bytes)
        artifact = EvidenceArtifact(
            test_run_id=run.id,
            artifact_type=ArtifactType.REPORT,
            storage_path=path,
        )
        await self._repo.create_evidence_artifact(artifact)
        stored_paths.append(path)

        logger.info(
            "Stored %d evidence artifacts for run %s", len(stored_paths), run_id
        )
        return ev_dict

    def _resolve_start_command(self, project: Any) -> str:
        """Extract start_command from project.repo_metadata or use default."""
        if project and project.repo_metadata:
            cmd = project.repo_metadata.get("start_command")
            if cmd:
                return str(cmd)
        return "npm start"

    def _resolve_repo_path(self, project: Any) -> str:
        """Extract the local repo path (temp dir) from project metadata."""
        if project and project.repo_metadata:
            path = project.repo_metadata.get("repo_path")
            if path:
                return str(path)
        # Fall back to empty string — E2BSandboxRunner handles gracefully
        return ""

    async def _get_project_for_test(self, test_def: Any, owner_id: str) -> Any:
        """Load the parent project for a test definition."""
        try:
            project = await self._repo.get_project(test_def.project_id, owner_id)
            return project
        except Exception:
            return None
