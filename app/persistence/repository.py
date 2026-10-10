"""Abstract persistence interface.

In-memory implementation used for Phase 1. Supabase implementation plugs in
via the same interface in Phase 2 — no service code changes required.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.models.domain import (
    EvidenceArtifact,
    Project,
    RepairProposal,
    TestDefinition,
    TestRun,
    TestVersion,
)


class PersistenceRepository(ABC):
    """Abstract persistence contract. All ownership filtering is enforced here."""

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_project(self, project: Project) -> Project: ...

    @abstractmethod
    async def get_project(self, project_id: uuid.UUID, owner_id: str) -> Project | None: ...

    # ------------------------------------------------------------------
    # Test Definitions
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_test_definition(self, test_def: TestDefinition) -> TestDefinition: ...

    @abstractmethod
    async def get_test_definition(
        self, test_id: uuid.UUID, owner_id: str
    ) -> TestDefinition | None: ...

    @abstractmethod
    async def update_active_version(
        self, test_id: uuid.UUID, version_id: uuid.UUID, owner_id: str
    ) -> None: ...

    # ------------------------------------------------------------------
    # Test Versions (append-only — no update/delete)
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_test_version(self, version: TestVersion) -> TestVersion: ...

    @abstractmethod
    async def get_test_version(self, version_id: uuid.UUID) -> TestVersion | None: ...

    @abstractmethod
    async def get_latest_version_number(self, test_id: uuid.UUID) -> int:
        """Return the highest version_number for the given test, or 0 if none."""
        ...

    @abstractmethod
    async def get_versions_for_test(self, test_id: uuid.UUID) -> list[TestVersion]: ...

    # ------------------------------------------------------------------
    # Test Runs (append-only evidence — status can advance)
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_test_run(self, run: TestRun) -> TestRun: ...

    @abstractmethod
    async def get_test_run(
        self, run_id: uuid.UUID, owner_id: str
    ) -> TestRun | None: ...

    @abstractmethod
    async def update_test_run(self, run: TestRun) -> TestRun: ...

    @abstractmethod
    async def get_runs_for_test(
        self, test_id: uuid.UUID, owner_id: str
    ) -> list[TestRun]: ...

    # ------------------------------------------------------------------
    # Evidence Artifacts (append-only)
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_evidence_artifact(
        self, artifact: EvidenceArtifact
    ) -> EvidenceArtifact: ...

    @abstractmethod
    async def get_artifacts_for_run(
        self, run_id: uuid.UUID
    ) -> list[EvidenceArtifact]: ...

    # ------------------------------------------------------------------
    # Repair Proposals
    # ------------------------------------------------------------------
    @abstractmethod
    async def create_repair_proposal(self, proposal: RepairProposal) -> RepairProposal: ...

    @abstractmethod
    async def get_repair_proposal(
        self, proposal_id: uuid.UUID
    ) -> RepairProposal | None: ...

    @abstractmethod
    async def update_repair_proposal(self, proposal: RepairProposal) -> RepairProposal: ...

    @abstractmethod
    async def get_proposals_for_run(
        self, run_id: uuid.UUID
    ) -> list[RepairProposal]: ...
