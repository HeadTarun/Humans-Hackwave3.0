"""API response schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from .domain import (
    ActionPlan,
    ArtifactType,
    FailureCategory,
    RepairValidationStatus,
    RequiredAssertion,
    RunStatus,
)


class ProjectResponse(BaseModel):
    id: uuid.UUID
    owner_id: str
    name: str
    repo_metadata: Optional[dict[str, Any]] = None
    created_at: datetime


class TestVersionResponse(BaseModel):
    id: uuid.UUID
    test_definition_id: uuid.UUID
    version_number: int
    action_plan: ActionPlan
    assertions_snapshot: list[RequiredAssertion]
    repository_revision: Optional[str] = None
    created_at: datetime
    created_by: str
    change_reason: str


class TestDefinitionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    owner_id: str
    name: str
    original_intent: str
    required_assertions: list[RequiredAssertion]
    preconditions: Optional[dict[str, Any]] = None
    active_version_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    current_version: Optional[TestVersionResponse] = None


class RunResponse(BaseModel):
    id: uuid.UUID
    test_definition_id: uuid.UUID
    test_version_id: uuid.UUID
    status: RunStatus
    classification: Optional[FailureCategory] = None
    confidence: Optional[float] = None
    summary: Optional[str] = None
    environment_ref: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    parent_run_id: Optional[uuid.UUID] = None
    created_at: datetime


class EvidenceArtifactResponse(BaseModel):
    id: uuid.UUID
    test_run_id: uuid.UUID
    artifact_type: ArtifactType
    storage_path: str
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime


class RepairProposalResponse(BaseModel):
    id: uuid.UUID
    test_run_id: uuid.UUID
    from_version_id: uuid.UUID
    proposed_plan: ActionPlan
    reason: str
    evidence_refs: list[str]
    confidence: float
    validation_status: RepairValidationStatus
    validation_run_id: Optional[uuid.UUID] = None
    created_at: datetime


class RunHistoryResponse(BaseModel):
    test_definition_id: uuid.UUID
    total: int
    runs: list[RunResponse]


class ErrorResponse(BaseModel):
    detail: str
    request_id: Optional[str] = None
