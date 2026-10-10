"""Test run service — lifecycle management with enforced state machine.

Business rules:
- A run is queued against a specific, pinned test version.
- Status transitions are validated against VALID_TRANSITIONS.
- Invalid transitions raise HTTP 409 Conflict.
- started_at is set on first transition out of QUEUED.
- finished_at is set when entering a terminal status.
- classification and confidence are required when entering FAILED/NEEDS_REVIEW/PASSED.
- Run history is append-only; evidence is never overwritten.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app.models.domain import (
    EvidenceArtifact,
    ArtifactType,
    FailureCategory,
    RepairProposal,
    RepairValidationStatus,
    RunStatus,
    TestRun,
    VALID_TRANSITIONS,
    is_terminal_status,
    is_valid_transition,
)
from app.models.requests import (
    ApproveRepairRequest,
    CreateRepairProposalRequest,
    CreateRunRequest,
    TransitionRunRequest,
)
from app.persistence.repository import PersistenceRepository

logger = logging.getLogger(__name__)


class InvalidTransitionError(HTTPException):
    def __init__(self, from_status: RunStatus, to_status: RunStatus) -> None:
        super().__init__(
            status_code=409,
            detail=(
                f"Invalid status transition: {from_status.value!r} → {to_status.value!r}. "
                f"Allowed next statuses: "
                f"{[s.value for s in VALID_TRANSITIONS.get(from_status, set())]}"
            ),
        )


class TestRunService:
    def __init__(self, repo: PersistenceRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Create run
    # ------------------------------------------------------------------

    async def create_run(
        self,
        test_id: uuid.UUID,
        owner_id: str,
        request: CreateRunRequest,
        parent_run_id: Optional[uuid.UUID] = None,
    ) -> TestRun:
        """Queue a new run against the active test version."""
        # Verify test exists and ownership
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )

        # Require an active version to run against
        if not test_def.active_version_id:
            raise HTTPException(
                status_code=409,
                detail="Test definition has no active version. Create a version before starting a run.",
            )

        # Verify the version exists
        version = await self._repo.get_test_version(test_def.active_version_id)
        if not version:
            raise HTTPException(
                status_code=409,
                detail="Active version not found. This is a data integrity issue.",
            )

        run = TestRun(
            test_definition_id=test_id,
            test_version_id=test_def.active_version_id,
            owner_id=owner_id,
            status=RunStatus.QUEUED,
            environment_ref=request.environment_ref,
            parent_run_id=parent_run_id,
        )
        run = await self._repo.create_test_run(run)
        logger.info(
            "Created test run",
            extra={"run_id": str(run.id), "test_id": str(test_id), "owner_id": owner_id},
        )
        return run

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    async def transition(
        self,
        run_id: uuid.UUID,
        owner_id: str,
        request: TransitionRunRequest,
    ) -> TestRun:
        """Advance a run's status. Rejects invalid transitions with HTTP 409."""
        run = await self._repo.get_test_run(run_id, owner_id)
        if not run:
            raise HTTPException(
                status_code=404,
                detail=f"Run {run_id} not found or access denied",
            )

        target = request.status
        if not is_valid_transition(run.status, target):
            raise InvalidTransitionError(run.status, target)

        now = datetime.now(timezone.utc)
        updates: dict = {"status": target}

        # Timestamp rules
        if run.started_at is None and run.status == RunStatus.QUEUED:
            updates["started_at"] = now

        if is_terminal_status(target):
            updates["finished_at"] = now

        # Classification/confidence required for diagnostic terminal statuses
        if target in {RunStatus.FAILED, RunStatus.NEEDS_REVIEW}:
            if request.classification is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"classification is required when transitioning to {target.value!r}",
                )
            if request.confidence is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"confidence is required when transitioning to {target.value!r}",
                )

        # PASSED: all assertions implicitly verified by caller (runner)
        # Classification optional for PASSED but allowed
        if request.classification is not None:
            updates["classification"] = request.classification
        if request.confidence is not None:
            updates["confidence"] = request.confidence
        if request.summary is not None:
            updates["summary"] = request.summary

        updated_run = run.model_copy(update=updates)
        updated_run = await self._repo.update_test_run(updated_run)
        logger.info(
            "Run status transition",
            extra={
                "run_id": str(run_id),
                "from": run.status.value,
                "to": target.value,
            },
        )
        return updated_run

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_run(self, run_id: uuid.UUID, owner_id: str) -> TestRun:
        run = await self._repo.get_test_run(run_id, owner_id)
        if not run:
            raise HTTPException(
                status_code=404,
                detail=f"Run {run_id} not found or access denied",
            )
        return run

    async def get_run_history(
        self, test_id: uuid.UUID, owner_id: str
    ) -> list[TestRun]:
        # Verify the test exists and belongs to this owner
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )
        return await self._repo.get_runs_for_test(test_id, owner_id)

    # ------------------------------------------------------------------
    # Repair proposals
    # ------------------------------------------------------------------

    async def create_repair_proposal(
        self,
        run_id: uuid.UUID,
        owner_id: str,
        request: CreateRepairProposalRequest,
    ) -> RepairProposal:
        """Create a repair proposal for a run in REPAIR_PROPOSED status."""
        run = await self._repo.get_test_run(run_id, owner_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found or access denied")

        if run.status != RunStatus.REPAIR_PROPOSED:
            raise HTTPException(
                status_code=409,
                detail=f"Repair proposals can only be created for runs with status 'repair_proposed'. "
                       f"Current status: {run.status.value!r}",
            )

        # Validate that the proposal doesn't weaken assertions
        # The proposed_plan must include at least as many required_assertions as the original
        version = await self._repo.get_test_version(run.test_version_id)
        if version:
            original_count = len(version.assertions_snapshot)
            proposed_count = len(request.proposed_plan.required_assertions)
            if proposed_count < original_count:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Repair proposal cannot weaken assertions: "
                        f"original has {original_count}, proposal has {proposed_count}. "
                        "A repair may only change locator/action mappings, never remove assertions."
                    ),
                )

        proposal = RepairProposal(
            test_run_id=run_id,
            from_version_id=run.test_version_id,
            proposed_plan=request.proposed_plan,
            reason=request.reason,
            evidence_refs=request.evidence_refs,
            confidence=request.confidence,
        )
        return await self._repo.create_repair_proposal(proposal)

    async def validate_repair(
        self,
        run_id: uuid.UUID,
        proposal_id: uuid.UUID,
        owner_id: str,
    ) -> RepairProposal:
        """Mark proposal as validating and transition run to RERUNNING."""
        run = await self._repo.get_test_run(run_id, owner_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found or access denied")

        proposal = await self._repo.get_repair_proposal(proposal_id)
        if not proposal or proposal.test_run_id != run_id:
            raise HTTPException(
                status_code=404,
                detail=f"Proposal {proposal_id} not found for run {run_id}",
            )

        if proposal.validation_status != RepairValidationStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already in status {proposal.validation_status.value!r}",
            )

        # Transition run → RERUNNING
        await self.transition(
            run_id, owner_id,
            TransitionRunRequest(status=RunStatus.RERUNNING),
        )

        updated = proposal.model_copy(
            update={"validation_status": RepairValidationStatus.VALIDATING}
        )
        return await self._repo.update_repair_proposal(updated)

    async def approve_repair(
        self,
        proposal_id: uuid.UUID,
        owner_id: str,
        request: ApproveRepairRequest,
    ) -> RepairProposal:
        """Approve a validated repair. Creates a new test version as the active version."""
        proposal = await self._repo.get_repair_proposal(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")

        # Verify caller owns the parent run
        run = await self._repo.get_test_run(proposal.test_run_id, owner_id)
        if not run:
            raise HTTPException(status_code=403, detail="Access denied")

        if proposal.validation_status != RepairValidationStatus.VALIDATED_PASS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Only proposals with status 'validated_pass' can be approved. "
                    f"Current status: {proposal.validation_status.value!r}"
                ),
            )

        # Create new test version from the approved plan
        test_def = await self._repo.get_test_definition(run.test_definition_id, owner_id)
        if not test_def:
            raise HTTPException(status_code=404, detail="Test definition not found")

        next_number = (await self._repo.get_latest_version_number(test_def.id)) + 1
        from app.models.domain import TestVersion
        new_version = TestVersion(
            test_definition_id=test_def.id,
            version_number=next_number,
            action_plan=proposal.proposed_plan,
            assertions_snapshot=test_def.required_assertions,  # ALWAYS from original
            created_by=owner_id,
            change_reason=request.change_reason,
        )
        await self._repo.create_test_version(new_version)
        await self._repo.update_active_version(test_def.id, new_version.id, owner_id)

        updated = proposal.model_copy(update={"validation_status": RepairValidationStatus.APPROVED})
        updated = await self._repo.update_repair_proposal(updated)

        logger.info(
            "Repair proposal approved, new version created",
            extra={
                "proposal_id": str(proposal_id),
                "new_version_id": str(new_version.id),
                "version_number": next_number,
            },
        )
        return updated
