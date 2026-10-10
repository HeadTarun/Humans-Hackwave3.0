"""Core domain models and enums for the self-healing E2E testing platform."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    """Test run lifecycle statuses. Transitions are validated by the state machine."""
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    COLLECTING_EVIDENCE = "collecting_evidence"
    CLASSIFYING = "classifying"
    REPAIR_PROPOSED = "repair_proposed"
    RERUNNING = "rerunning"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    ENVIRONMENT_FAILED = "environment_failed"


class FailureCategory(str, Enum):
    """Failure classification categories (fixed MVP enum)."""
    LOCATOR_DRIFT = "LOCATOR_DRIFT"
    UI_FUNCTIONAL_REGRESSION = "UI_FUNCTIONAL_REGRESSION"
    API_FAILURE = "API_FAILURE"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    AMBIGUOUS_REQUIRES_REVIEW = "AMBIGUOUS_REQUIRES_REVIEW"


class ArtifactType(str, Enum):
    """Types of evidence artifacts.
    Must stay in sync with evidence_artifacts.artifact_type CHECK constraint (migration 003).
    """
    SCREENSHOT             = "screenshot"
    TRACE                  = "trace"
    DOM_SNAPSHOT           = "dom_snapshot"
    CONSOLE_LOG            = "console_log"
    NETWORK_LOG            = "network_log"
    REPORT                 = "report"
    # Adaptive healing additions (migration 003)
    REPAIR_CANDIDATE       = "repair_candidate"       # auto-repair candidate JSON
    ACCESSIBILITY_SNAPSHOT = "accessibility_snapshot" # Playwright a11y tree JSON


class RepairValidationStatus(str, Enum):
    """Lifecycle of a repair proposal."""
    PENDING = "pending"
    VALIDATING = "validating"
    VALIDATED_PASS = "validated_pass"
    VALIDATED_FAIL = "validated_fail"
    APPROVED = "approved"
    REJECTED = "rejected"


class ActionType(str, Enum):
    """Allowlisted browser action types. ONLY these are executable — never arbitrary code."""
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    ASSERT_VISIBLE = "assert_visible"
    ASSERT_TEXT = "assert_text"
    ASSERT_URL = "assert_url"
    WAIT = "wait"


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.PREPARING},
    RunStatus.PREPARING: {RunStatus.RUNNING, RunStatus.ENVIRONMENT_FAILED},
    RunStatus.RUNNING: {RunStatus.COLLECTING_EVIDENCE, RunStatus.ENVIRONMENT_FAILED},
    RunStatus.COLLECTING_EVIDENCE: {RunStatus.CLASSIFYING},
    RunStatus.CLASSIFYING: {
        RunStatus.PASSED,
        RunStatus.FAILED,
        RunStatus.REPAIR_PROPOSED,
        RunStatus.NEEDS_REVIEW,
    },
    RunStatus.REPAIR_PROPOSED: {RunStatus.RERUNNING},
    RunStatus.RERUNNING: {
        RunStatus.PASSED,
        RunStatus.FAILED,
        RunStatus.NEEDS_REVIEW,
    },
    # Terminal states — no outgoing transitions
    RunStatus.PASSED: set(),
    RunStatus.FAILED: set(),
    RunStatus.NEEDS_REVIEW: set(),
    RunStatus.ENVIRONMENT_FAILED: set(),
}


def is_valid_transition(from_status: RunStatus, to_status: RunStatus) -> bool:
    """Return True if transitioning from_status → to_status is allowed."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


def is_terminal_status(status: RunStatus) -> bool:
    """Return True if status has no further valid outgoing transitions."""
    return len(VALID_TRANSITIONS.get(status, set())) == 0


# ---------------------------------------------------------------------------
# Action Plan Models
# ---------------------------------------------------------------------------

class ActionTarget(BaseModel):
    """Identifies a UI element by role/name/text — NOT raw CSS/XPath selectors."""
    role: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    selector: Optional[str] = None  # CSS only, last resort; validated on use

    @model_validator(mode="after")
    def at_least_one_field(self) -> "ActionTarget":
        if not any([self.role, self.name, self.text, self.url, self.selector]):
            raise ValueError("ActionTarget must specify at least one targeting field")
        return self


class ActionStep(BaseModel):
    """A single step in a structured, validated action plan."""
    action: ActionType  # Enforced against allowlist
    target: ActionTarget | str  # str accepted for simple URL targets (navigate)
    value: Optional[str] = None  # For fill/select
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)


class RequiredAssertion(BaseModel):
    """An assertion that MUST pass for a run to be considered successful.
    These are immutable source-of-truth inputs — they can never be removed or weakened.
    """
    type: str  # "visible" | "text" | "url" | "api_status"
    target: ActionTarget | str
    expected_value: Optional[str] = None
    description: Optional[str] = None


class ActionPlan(BaseModel):
    """Complete validated action plan. JSON only — no executable code."""
    steps: list[ActionStep] = Field(min_length=1)
    required_assertions: list[RequiredAssertion] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def validate_action_types(cls, v: list[ActionStep]) -> list[ActionStep]:
        # Belt-and-suspenders: ActionType enum already rejects unknowns at parse time
        for step in v:
            if not isinstance(step.action, ActionType):
                raise ValueError(f"Unsupported action type: {step.action!r}")
        return v


# ---------------------------------------------------------------------------
# Domain Entity Models
# ---------------------------------------------------------------------------

class Project(BaseModel):
    """Top-level grouping for tests and repository metadata."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    owner_id: str
    name: str = Field(min_length=1, max_length=255)
    repo_metadata: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TestDefinition(BaseModel):
    """Test definition with immutable natural-language intent and required assertions."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    project_id: uuid.UUID
    owner_id: str
    name: str = Field(min_length=1, max_length=255)
    # Immutable source-of-truth — write-once, never updated
    original_intent: str = Field(min_length=1, max_length=5000)
    required_assertions: list[RequiredAssertion] = Field(min_length=1)
    preconditions: Optional[dict[str, Any]] = None
    active_version_id: Optional[uuid.UUID] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TestVersion(BaseModel):
    """Immutable, append-only record of an action plan at a point in time.
    Generated Playwright steps/locators live here — NOT in the test definition.
    Repairs create new versions; they never modify existing ones.
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    test_definition_id: uuid.UUID
    version_number: int = Field(ge=1)
    action_plan: ActionPlan
    assertions_snapshot: list[RequiredAssertion]  # Snapshot of required_assertions at creation
    repository_revision: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str
    change_reason: str = Field(min_length=1, max_length=1000)


class TestRun(BaseModel):
    """A single test execution record. Append-only — never overwrite evidence."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    test_definition_id: uuid.UUID
    test_version_id: uuid.UUID
    owner_id: str
    status: RunStatus = RunStatus.QUEUED
    classification: Optional[FailureCategory] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    summary: Optional[str] = None
    environment_ref: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    parent_run_id: Optional[uuid.UUID] = None  # Set for repair reruns
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvidenceArtifact(BaseModel):
    """Reference to a run artifact stored in private Supabase Storage."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    test_run_id: uuid.UUID
    artifact_type: ArtifactType
    storage_path: str  # Private storage path — never a public URL
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RepairProposal(BaseModel):
    """A candidate repair that MUST be validated before it can be approved.
    Proposals never weaken or remove required assertions.
    Approval creates a new TestVersion — it never modifies the original.
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    test_run_id: uuid.UUID
    from_version_id: uuid.UUID
    proposed_plan: ActionPlan
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    validation_status: RepairValidationStatus = RepairValidationStatus.PENDING
    validation_run_id: Optional[uuid.UUID] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
