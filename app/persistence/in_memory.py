"""In-memory persistence implementation for Phase 1.

Every ownership check mirrors what the Supabase RLS policies enforce.
This implementation is used in tests and local development.
Replace with SupabaseRepository in Phase 2 — same interface, zero service changes.
"""
from __future__ import annotations

import uuid

from app.models.domain import (
    EvidenceArtifact,
    Project,
    RepairProposal,
    TestDefinition,
    TestRun,
    TestVersion,
)
from app.persistence.repository import PersistenceRepository


class InMemoryRepository(PersistenceRepository):
    def __init__(self) -> None:
        self._projects: dict[uuid.UUID, Project] = {}
        self._test_definitions: dict[uuid.UUID, TestDefinition] = {}
        self._test_versions: dict[uuid.UUID, TestVersion] = {}
        self._test_runs: dict[uuid.UUID, TestRun] = {}
        self._evidence_artifacts: dict[uuid.UUID, EvidenceArtifact] = {}
        self._repair_proposals: dict[uuid.UUID, RepairProposal] = {}

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    async def create_project(self, project: Project) -> Project:
        self._projects[project.id] = project
        return project

    async def get_project(
        self, project_id: uuid.UUID, owner_id: str
    ) -> Project | None:
        p = self._projects.get(project_id)
        # Simulates RLS: owner_id = auth.uid()
        return p if (p and p.owner_id == owner_id) else None

    async def update_project_metadata(
        self, project_id: uuid.UUID, repo_metadata: dict, owner_id: str
    ) -> None:
        p = self._projects.get(project_id)
        if p and p.owner_id == owner_id:
            self._projects[project_id] = p.model_copy(update={"repo_metadata": repo_metadata})

    # ------------------------------------------------------------------
    # Test Definitions
    # ------------------------------------------------------------------
    async def create_test_definition(self, test_def: TestDefinition) -> TestDefinition:
        self._test_definitions[test_def.id] = test_def
        return test_def

    async def get_test_definition(
        self, test_id: uuid.UUID, owner_id: str
    ) -> TestDefinition | None:
        td = self._test_definitions.get(test_id)
        return td if (td and td.owner_id == owner_id) else None

    async def update_active_version(
        self, test_id: uuid.UUID, version_id: uuid.UUID, owner_id: str
    ) -> None:
        td = self._test_definitions.get(test_id)
        if not td or td.owner_id != owner_id:
            return
        # Only update mutable fields — never touch original_intent or required_assertions
        from datetime import datetime, timezone
        updated = td.model_copy(
            update={"active_version_id": version_id, "updated_at": datetime.now(timezone.utc)}
        )
        self._test_definitions[test_id] = updated

    # ------------------------------------------------------------------
    # Test Versions (append-only)
    # ------------------------------------------------------------------
    async def create_test_version(self, version: TestVersion) -> TestVersion:
        self._test_versions[version.id] = version
        return version

    async def get_test_version(self, version_id: uuid.UUID) -> TestVersion | None:
        return self._test_versions.get(version_id)

    async def get_latest_version_number(self, test_id: uuid.UUID) -> int:
        versions = [
            v for v in self._test_versions.values()
            if v.test_definition_id == test_id
        ]
        return max((v.version_number for v in versions), default=0)

    async def get_versions_for_test(self, test_id: uuid.UUID) -> list[TestVersion]:
        return sorted(
            [v for v in self._test_versions.values() if v.test_definition_id == test_id],
            key=lambda v: v.version_number,
        )

    # ------------------------------------------------------------------
    # Test Runs
    # ------------------------------------------------------------------
    async def create_test_run(self, run: TestRun) -> TestRun:
        self._test_runs[run.id] = run
        return run

    async def get_test_run(
        self, run_id: uuid.UUID, owner_id: str
    ) -> TestRun | None:
        r = self._test_runs.get(run_id)
        return r if (r and r.owner_id == owner_id) else None

    async def update_test_run(self, run: TestRun) -> TestRun:
        self._test_runs[run.id] = run
        return run

    async def get_runs_for_test(
        self, test_id: uuid.UUID, owner_id: str
    ) -> list[TestRun]:
        return sorted(
            [
                r for r in self._test_runs.values()
                if r.test_definition_id == test_id and r.owner_id == owner_id
            ],
            key=lambda r: r.created_at,
        )

    # ------------------------------------------------------------------
    # Evidence Artifacts (append-only)
    # ------------------------------------------------------------------
    async def create_evidence_artifact(
        self, artifact: EvidenceArtifact
    ) -> EvidenceArtifact:
        self._evidence_artifacts[artifact.id] = artifact
        return artifact

    async def get_artifacts_for_run(
        self, run_id: uuid.UUID
    ) -> list[EvidenceArtifact]:
        return [
            a for a in self._evidence_artifacts.values()
            if a.test_run_id == run_id
        ]

    # ------------------------------------------------------------------
    # Repair Proposals
    # ------------------------------------------------------------------
    async def create_repair_proposal(self, proposal: RepairProposal) -> RepairProposal:
        self._repair_proposals[proposal.id] = proposal
        return proposal

    async def get_repair_proposal(
        self, proposal_id: uuid.UUID
    ) -> RepairProposal | None:
        return self._repair_proposals.get(proposal_id)

    async def update_repair_proposal(self, proposal: RepairProposal) -> RepairProposal:
        self._repair_proposals[proposal.id] = proposal
        return proposal

    async def get_proposals_for_run(self, run_id: uuid.UUID) -> list[RepairProposal]:
        return [
            p for p in self._repair_proposals.values()
            if p.test_run_id == run_id
        ]
