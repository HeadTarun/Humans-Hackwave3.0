"""Repair proposal routes."""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_owner_id, get_repository
from app.models.requests import ApproveRepairRequest, CreateRepairProposalRequest
from app.models.responses import RepairProposalListResponse, RepairProposalResponse
from app.persistence.repository import PersistenceRepository
from app.services.test_run_service import TestRunService

router = APIRouter()


def _make_service(repo: PersistenceRepository) -> TestRunService:
    return TestRunService(repo)


@router.get(
    "/runs/{run_id}/repair-proposals",
    response_model=RepairProposalListResponse,
    tags=["repairs"],
)
async def list_repair_proposals(
    run_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RepairProposalListResponse:
    svc = _make_service(repo)
    # Verify run exists and belongs to owner
    await svc.get_run(run_id, owner_id)
    proposals = await repo.get_proposals_for_run(run_id)
    return RepairProposalListResponse(
        test_run_id=run_id,
        total=len(proposals),
        proposals=[RepairProposalResponse(**p.model_dump()) for p in proposals],
    )


@router.get(
    "/repair-proposals/{proposal_id}",
    response_model=RepairProposalResponse,
    tags=["repairs"],
)
async def get_repair_proposal(
    proposal_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> RepairProposalResponse:
    proposal = await repo.get_repair_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    # Verify owner has access to parent run
    svc = _make_service(repo)
    await svc.get_run(proposal.test_run_id, owner_id)
    return RepairProposalResponse(**proposal.model_dump())


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
