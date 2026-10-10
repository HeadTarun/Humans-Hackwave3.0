"""Run lifecycle routes."""
import uuid

from fastapi import APIRouter, Depends

from app.dependencies import get_owner_id, get_repository
from app.models.requests import CreateRunRequest, TransitionRunRequest
from app.models.responses import RunHistoryResponse, RunResponse
from app.persistence.repository import PersistenceRepository
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
) -> RunResponse:
    svc = _make_service(repo)
    run = await svc.create_run(test_id, owner_id, body)
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
    """Advance a run's status. Used internally by the runner and for lifecycle testing."""
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
