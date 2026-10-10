"""Repair proposal routes."""
import uuid

from fastapi import APIRouter, Depends

from app.dependencies import get_owner_id, get_repository
from app.models.requests import ApproveRepairRequest, CreateRepairProposalRequest
from app.models.responses import RepairProposalResponse
from app.persistence.repository import PersistenceRepository
from app.services.test_run_service import TestRunService

router = APIRouter()


def _make_service(repo: PersistenceRepository) -> TestRunService:
    return TestRunService(repo)


@router.post(
    "/runs/{run_id}/repair-proposals",
    response_model=RepairProposalResponse,
    status_code=201,
    tags=["repairs"],
)
async def create_repair_proposal(
    run_id: uuid.UUID,
    body: CreateRepairProposalRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RepairProposalResponse:
    svc = _make_service(repo)
    proposal = await svc.create_repair_proposal(run_id, owner_id, body)
    return RepairProposalResponse(**proposal.model_dump())


@router.post(
    "/runs/{run_id}/repair-proposals/{proposal_id}/validate",
    response_model=RepairProposalResponse,
    tags=["repairs"],
)
async def validate_repair(
    run_id: uuid.UUID,
    proposal_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RepairProposalResponse:
    svc = _make_service(repo)
    proposal = await svc.validate_repair(run_id, proposal_id, owner_id)
    return RepairProposalResponse(**proposal.model_dump())


@router.post(
    "/repair-proposals/{proposal_id}/approve",
    response_model=RepairProposalResponse,
    tags=["repairs"],
)
async def approve_repair(
    proposal_id: uuid.UUID,
    body: ApproveRepairRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RepairProposalResponse:
    svc = _make_service(repo)
    proposal = await svc.approve_repair(proposal_id, owner_id, body)
    return RepairProposalResponse(**proposal.model_dump())
