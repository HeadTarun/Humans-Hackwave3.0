"""API request schemas — validated inputs from callers."""
from typing import Any, Optional

from pydantic import BaseModel, Field

from .domain import ActionPlan, RequiredAssertion, RunStatus, FailureCategory


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repo_metadata: Optional[dict[str, Any]] = None


class CreateTestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    original_intent: str = Field(min_length=1, max_length=5000)
    required_assertions: list[RequiredAssertion] = Field(min_length=1)
    preconditions: Optional[dict[str, Any]] = None
    # Optional initial action plan — if provided, creates version 1 immediately
    action_plan: Optional[ActionPlan] = None


class CreateRunRequest(BaseModel):
    environment_ref: Optional[str] = None
    start_command: Optional[str] = None  # Override project.repo_metadata["start_command"]
    callback_url: Optional[str] = None  # Webhook/gateway callback for live progress events


class TransitionRunRequest(BaseModel):
    """Advance a run's status. Used internally and for testing the lifecycle."""
    status: RunStatus
    classification: Optional[FailureCategory] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    summary: Optional[str] = None


class CreateRepairProposalRequest(BaseModel):
    proposed_plan: ActionPlan
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ApproveRepairRequest(BaseModel):
    change_reason: str = Field(min_length=1, max_length=1000)
