"""Run lifecycle routes.

Phase 3 change: POST /tests/{test_id}/runs now uses RunOrchestrator when E2B
is configured. When E2B is not set (local dev / tests), the orchestrator still
runs with FakeSandboxRunner + FakeBrowserRunner, driving the full state machine
automatically from a single API call.
"""
import uuid

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.dependencies import (
    get_artifact_store,
    get_browser_runner,
    get_owner_id,
    get_repository,
    get_sandbox_runner,
)
from app.models.requests import CreateRunRequest, TransitionRunRequest
from app.models.responses import RunHistoryResponse, RunResponse
from app.persistence.repository import PersistenceRepository
from app.services.interfaces import ArtifactStore, BrowserRunner, SandboxRunner
from app.services.run_orchestrator import DeterministicClassifier, RunOrchestrator
from app.services.test_run_service import TestRunService

router = APIRouter()


def _make_service(repo: PersistenceRepository) -> TestRunService:
    return TestRunService(repo)


@router.post(
    "/tests/{test_id}/runs",
    response_model=RunResponse,
    status_code=201,
    tags=["runs"],
)
async def create_run(
    test_id: uuid.UUID,
    body: CreateRunRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
    sandbox: SandboxRunner = Depends(get_sandbox_runner),
    browser: BrowserRunner = Depends(get_browser_runner),
    artifact_store: ArtifactStore = Depends(get_artifact_store),
    settings: Settings = Depends(get_settings),
) -> RunResponse:
    """Create and execute a test run.

    With E2B configured: creates real sandbox, executes Playwright, collects evidence.
    Without E2B: uses fake implementations but drives the same state machine.

    Either way, the run progresses through the full lifecycle in one request.
    """
    orchestrator = RunOrchestrator(
        repo=repo,
        sandbox=sandbox,
        browser=browser,
        classifier=DeterministicClassifier(),
        artifact_store=artifact_store,
    )
    run = await orchestrator.execute_run(
        test_id=test_id,
        owner_id=owner_id,
        environment_ref=body.environment_ref,
        start_command=body.start_command,
    )
    return RunResponse(**run.model_dump())


@router.get(
    "/runs/{run_id}",
    response_model=RunResponse,
    tags=["runs"],
)
async def get_run(
    run_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RunResponse:
    svc = _make_service(repo)
    run = await svc.get_run(run_id, owner_id)
    return RunResponse(**run.model_dump())


@router.post(
    "/runs/{run_id}/transition",
    response_model=RunResponse,
    tags=["runs"],
)
async def transition_run(
    run_id: uuid.UUID,
    body: TransitionRunRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RunResponse:
    """Advance a run's status manually. Useful for repair flows and lifecycle testing."""
    svc = _make_service(repo)
    run = await svc.transition(run_id, owner_id, body)
    return RunResponse(**run.model_dump())


@router.get(
    "/tests/{test_id}/runs",
    response_model=RunHistoryResponse,
    tags=["runs"],
)
async def get_run_history(
    test_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RunHistoryResponse:
    svc = _make_service(repo)
    runs = await svc.get_run_history(test_id, owner_id)
    return RunHistoryResponse(
        test_definition_id=test_id,
        total=len(runs),
        runs=[RunResponse(**r.model_dump()) for r in runs],
    )
