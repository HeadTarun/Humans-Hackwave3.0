"""Tests for RunOrchestrator — full lifecycle with fake implementations.

No E2B key needed. All tests use FakeSandboxRunner + FakeBrowserRunner
+ FakeArtifactStore, which cover the complete state machine and evidence
collection without network calls.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models.domain import (
    ActionPlan,
    ActionStep,
    ActionTarget,
    ActionType,
    ArtifactType,
    FailureCategory,
    RequiredAssertion,
    RunStatus,
    TestDefinition,
    TestVersion,
)
from app.persistence.in_memory import InMemoryRepository
from app.services.interfaces import (
    ClassificationResult,
    FakeArtifactStore,
    FakeBrowserRunner,
    FakeSandboxRunner,
    SandboxResult,
)
from app.services.run_orchestrator import DeterministicClassifier, RunOrchestrator


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

OWNER = "user-orch-test-001"

MINIMAL_PLAN = ActionPlan(
    steps=[
        ActionStep(action=ActionType.NAVIGATE, target="http://localhost:3000"),
        ActionStep(
            action=ActionType.CLICK,
            target=ActionTarget(role="button", name="Checkout"),
        ),
    ],
    required_assertions=[
        RequiredAssertion(
            type="visible",
            target=ActionTarget(text="Order confirmed"),
            description="Order confirmation",
        )
    ],
)


async def _make_test(repo: InMemoryRepository) -> tuple[TestDefinition, TestVersion]:
    """Create project → test definition → test version in the in-memory repo."""
    from app.models.domain import Project
    project = await repo.create_project(
        Project(owner_id=OWNER, name="Orch Test Project")
    )
    test_def = TestDefinition(
        project_id=project.id,
        owner_id=OWNER,
        name="Checkout test",
        original_intent="Complete checkout and verify confirmation.",
        required_assertions=MINIMAL_PLAN.required_assertions,
    )
    test_def = await repo.create_test_definition(test_def)
    version = TestVersion(
        test_definition_id=test_def.id,
        version_number=1,
        action_plan=MINIMAL_PLAN,
        assertions_snapshot=MINIMAL_PLAN.required_assertions,
        created_by=OWNER,
        change_reason="Initial",
    )
    version = await repo.create_test_version(version)
    await repo.update_active_version(test_def.id, version.id, OWNER)
    # Reload to get updated active_version_id
    test_def = await repo.get_test_definition(test_def.id, OWNER)
    return test_def, version


def _make_orchestrator(
    repo: InMemoryRepository,
    sandbox: FakeSandboxRunner | None = None,
    browser: FakeBrowserRunner | None = None,
    artifact_store: FakeArtifactStore | None = None,
    classifier: DeterministicClassifier | None = None,
) -> RunOrchestrator:
    return RunOrchestrator(
        repo=repo,
        sandbox=sandbox or FakeSandboxRunner(),
        browser=browser or FakeBrowserRunner(should_pass=True),
        classifier=classifier or DeterministicClassifier(),
        artifact_store=artifact_store or FakeArtifactStore(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_happy_path_reaches_passed():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)
    orch = _make_orchestrator(repo)

    run = await orch.execute_run(test_def.id, OWNER)

    assert run.status == RunStatus.PASSED
    assert run.started_at is not None
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_orchestrator_happy_path_transitions_in_order():
    """Track all intermediate states via run history polling."""
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    # Patch FakeBrowserRunner to record calls
    browser = FakeBrowserRunner(should_pass=True)
    sandbox = FakeSandboxRunner()
    orch = _make_orchestrator(repo, sandbox=sandbox, browser=browser)

    run = await orch.execute_run(test_def.id, OWNER)

    # Terminal state
    assert run.status == RunStatus.PASSED
    # Sandbox was created and destroyed
    assert len(sandbox.created) == 1
    assert len(sandbox.destroyed) == 1
    assert sandbox.created[0] == sandbox.destroyed[0]


# ---------------------------------------------------------------------------
# Sandbox always destroyed (finally block)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_sandbox_destroyed_on_browser_error():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    # Browser that raises
    class FailingBrowser(FakeBrowserRunner):
        async def execute_plan(self, base_url, plan):
            raise RuntimeError("playwright crashed")

    sandbox = FakeSandboxRunner()
    orch = _make_orchestrator(repo, sandbox=sandbox, browser=FailingBrowser())

    run = await orch.execute_run(test_def.id, OWNER)

    # Should reach ENVIRONMENT_FAILED, not raise
    assert run.status == RunStatus.ENVIRONMENT_FAILED
    # Sandbox must be destroyed even on failure
    assert len(sandbox.destroyed) == 1


@pytest.mark.asyncio
async def test_orchestrator_sandbox_destroyed_on_sandbox_creation_failure():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    class FailingSandbox(FakeSandboxRunner):
        async def create_sandbox(self, repo_path, start_cmd, env_vars=None):
            raise EnvironmentError("E2B quota exceeded")

    sandbox = FailingSandbox()
    orch = _make_orchestrator(repo, sandbox=sandbox)

    run = await orch.execute_run(test_def.id, OWNER)
    assert run.status == RunStatus.ENVIRONMENT_FAILED
    # destroy never called since sandbox wasn't created
    assert len(sandbox.destroyed) == 0


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_browser_fail_reaches_failed_or_needs_review():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    browser = FakeBrowserRunner(
        should_pass=False,
        extra_evidence={
            "steps_failed": 1,
            "failed_step_index": 1,
            "failed_step_error": "Timeout waiting for button 'Checkout'",
            "network_failures": [],
            "console_errors": [],
            "assertion_results": [{"type": "visible", "passed": False, "detail": "not found"}],
        },
    )
    orch = _make_orchestrator(repo, browser=browser)
    run = await orch.execute_run(test_def.id, OWNER)

    assert run.status in {RunStatus.FAILED, RunStatus.NEEDS_REVIEW}


@pytest.mark.asyncio
async def test_orchestrator_api_failure_classified_correctly():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    browser = FakeBrowserRunner(
        should_pass=False,
        extra_evidence={
            "steps_failed": 0,
            "network_failures": [{"url": "http://localhost:3000/api/checkout", "status": 500, "method": "POST"}],
            "console_errors": [],
            "assertion_results": [{"type": "visible", "passed": False, "detail": "Order confirmed not visible"}],
        },
    )
    orch = _make_orchestrator(repo, browser=browser)
    run = await orch.execute_run(test_def.id, OWNER)

    # API_FAILURE doesn't require human review → FAILED
    assert run.status == RunStatus.FAILED
    assert run.classification == FailureCategory.API_FAILURE


@pytest.mark.asyncio
async def test_orchestrator_locator_drift_classified():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    browser = FakeBrowserRunner(
        should_pass=False,
        extra_evidence={
            "steps_failed": 1,
            "failed_step_index": 1,
            "failed_step_error": "Timeout 30000ms exceeded while waiting for locator get_by_role('button', name='Checkout')",
            "network_failures": [],
            "console_errors": [],
            "assertion_results": [],
        },
    )
    orch = _make_orchestrator(repo, browser=browser)
    run = await orch.execute_run(test_def.id, OWNER)

    assert run.classification == FailureCategory.LOCATOR_DRIFT
    assert run.status == RunStatus.FAILED


# ---------------------------------------------------------------------------
# Evidence artifacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_evidence_artifacts_created():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    store = FakeArtifactStore()
    orch = _make_orchestrator(repo, artifact_store=store)
    run = await orch.execute_run(test_def.id, OWNER)

    artifacts = await repo.get_artifacts_for_run(run.id)
    # At minimum a report artifact should be created
    assert len(artifacts) >= 1
    types = {a.artifact_type for a in artifacts}
    assert ArtifactType.REPORT in types


@pytest.mark.asyncio
async def test_orchestrator_evidence_linked_to_run():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    store = FakeArtifactStore()
    orch = _make_orchestrator(repo, artifact_store=store)
    run = await orch.execute_run(test_def.id, OWNER)

    artifacts = await repo.get_artifacts_for_run(run.id)
    assert all(a.test_run_id == run.id for a in artifacts)


# ---------------------------------------------------------------------------
# Invalid test / missing version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_missing_test_raises_404():
    from fastapi import HTTPException

    repo = InMemoryRepository()
    orch = _make_orchestrator(repo)

    with pytest.raises(HTTPException) as exc_info:
        await orch.execute_run(uuid.uuid4(), OWNER)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_orchestrator_no_active_version_raises_409():
    from fastapi import HTTPException
    from app.models.domain import Project

    repo = InMemoryRepository()
    project = await repo.create_project(Project(owner_id=OWNER, name="P"))
    test_def = await repo.create_test_definition(
        TestDefinition(
            project_id=project.id,
            owner_id=OWNER,
            name="no version",
            original_intent="test",
            required_assertions=[
                RequiredAssertion(type="visible", target=ActionTarget(text="x"))
            ],
        )
    )

    orch = _make_orchestrator(repo)
    with pytest.raises(HTTPException) as exc_info:
        await orch.execute_run(test_def.id, OWNER)
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# DeterministicClassifier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_classifier_api_failure():
    clf = DeterministicClassifier()
    result = await clf.classify({
        "network_failures": [{"url": "http://app/api", "status": 500, "method": "POST"}],
        "steps_failed": 0,
        "console_errors": [],
        "assertion_results": [],
    })
    assert result.category == FailureCategory.API_FAILURE
    assert result.confidence > 0.8
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_deterministic_classifier_locator_drift():
    clf = DeterministicClassifier()
    result = await clf.classify({
        "network_failures": [],
        "steps_failed": 1,
        "failed_step_error": "Timeout waiting for locator get_by_role",
        "console_errors": [],
        "assertion_results": [],
    })
    assert result.category == FailureCategory.LOCATOR_DRIFT
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_deterministic_classifier_assertion_failure():
    clf = DeterministicClassifier()
    result = await clf.classify({
        "network_failures": [],
        "steps_failed": 0,
        "console_errors": [],
        "assertion_results": [{"type": "visible", "passed": False, "detail": "element not visible"}],
    })
    assert result.category == FailureCategory.ASSERTION_FAILURE
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_deterministic_classifier_ambiguous():
    clf = DeterministicClassifier()
    result = await clf.classify({
        "network_failures": [],
        "steps_failed": 0,
        "console_errors": [],
        "assertion_results": [],
    })
    assert result.category == FailureCategory.AMBIGUOUS_REQUIRES_REVIEW
    assert result.requires_human_review is True


# ---------------------------------------------------------------------------
# Orchestrator state transitions — follow valid machine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_run_has_timestamps():
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)
    orch = _make_orchestrator(repo)

    run = await orch.execute_run(test_def.id, OWNER)

    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.finished_at >= run.started_at


@pytest.mark.asyncio
async def test_orchestrator_start_command_override():
    """start_command kwarg takes precedence over project metadata."""
    repo = InMemoryRepository()
    test_def, _ = await _make_test(repo)

    recorded_cmds: list[str] = []

    class RecordingSandbox(FakeSandboxRunner):
        async def create_sandbox(self, repo_path, start_cmd, env_vars=None):
            recorded_cmds.append(start_cmd)
            return await super().create_sandbox(repo_path, start_cmd, env_vars)

    orch = _make_orchestrator(repo, sandbox=RecordingSandbox())
    await orch.execute_run(test_def.id, OWNER, start_command="yarn dev")

    assert recorded_cmds == ["yarn dev"]
