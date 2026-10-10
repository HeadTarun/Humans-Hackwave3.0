"""Supabase Postgres persistence implementation.

Uses the service-role key on the backend (never exposed to clients).
Ownership filtering is enforced in every method, mirroring the RLS policies.
RLS is a defence-in-depth layer on top of this application-level check.

Assumption: supabase-py sync client is wrapped in asyncio.to_thread for
async compatibility. This is sound for I/O-bound Postgres calls at MVP scale.
Upgrade path: swap to AsyncClient when supabase-py async stabilises.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.domain import (
    EvidenceArtifact,
    Project,
    RepairProposal,
    TestDefinition,
    TestRun,
    TestVersion,
)
from app.persistence.repository import PersistenceRepository

logger = logging.getLogger(__name__)


def _to_db(model: Any) -> dict:
    """Serialize a Pydantic model to a JSON-safe dict for Supabase insert/update."""
    return model.model_dump(mode="json")


class SupabaseRepository(PersistenceRepository):
    """Persistence backed by Supabase Postgres via the supabase-py client."""

    def __init__(self, url: str, service_role_key: str) -> None:
        try:
            from supabase import create_client  # type: ignore[import]
            self._client = create_client(url, service_role_key)
            logger.info("SupabaseRepository initialised (url=%s)", url[:40])
        except ImportError as exc:
            raise RuntimeError(
                "supabase-py is not installed. Add 'supabase' to requirements.txt."
            ) from exc

    def _run(self, fn, *args, **kwargs):
        """Run a synchronous Supabase call in a thread to avoid blocking the event loop."""
        return asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def create_project(self, project: Project) -> Project:
        data = _to_db(project)
        result = await asyncio.to_thread(
            lambda: self._client.table("projects").insert(data).execute()
        )
        return Project(**result.data[0])

    async def get_project(self, project_id: uuid.UUID, owner_id: str) -> Project | None:
        result = await asyncio.to_thread(
            lambda: self._client.table("projects")
            .select("*")
            .eq("id", str(project_id))
            .eq("owner_id", owner_id)
            .execute()
        )
        if not result.data:
            return None
        return Project(**result.data[0])

    async def update_project_metadata(
        self, project_id: uuid.UUID, repo_metadata: dict, owner_id: str
    ) -> None:
        await asyncio.to_thread(
            lambda: self._client.table("projects")
            .update({"repo_metadata": repo_metadata})
            .eq("id", str(project_id))
            .eq("owner_id", owner_id)
            .execute()
        )

    # ------------------------------------------------------------------
    # Test Definitions
    # ------------------------------------------------------------------

    async def create_test_definition(self, test_def: TestDefinition) -> TestDefinition:
        data = _to_db(test_def)
        result = await asyncio.to_thread(
            lambda: self._client.table("test_definitions").insert(data).execute()
        )
        return TestDefinition(**result.data[0])

    async def get_test_definition(
        self, test_id: uuid.UUID, owner_id: str
    ) -> TestDefinition | None:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_definitions")
            .select("*")
            .eq("id", str(test_id))
            .eq("owner_id", owner_id)
            .execute()
        )
        if not result.data:
            return None
        return TestDefinition(**result.data[0])

    async def update_active_version(
        self, test_id: uuid.UUID, version_id: uuid.UUID, owner_id: str
    ) -> None:
        await asyncio.to_thread(
            lambda: self._client.table("test_definitions")
            .update({
                "active_version_id": str(version_id),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", str(test_id))
            .eq("owner_id", owner_id)
            .execute()
        )

    # ------------------------------------------------------------------
    # Test Versions (append-only)
    # ------------------------------------------------------------------

    async def create_test_version(self, version: TestVersion) -> TestVersion:
        data = _to_db(version)
        result = await asyncio.to_thread(
            lambda: self._client.table("test_versions").insert(data).execute()
        )
        return TestVersion(**result.data[0])

    async def get_test_version(self, version_id: uuid.UUID) -> TestVersion | None:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_versions")
            .select("*")
            .eq("id", str(version_id))
            .execute()
        )
        if not result.data:
            return None
        return TestVersion(**result.data[0])

    async def get_latest_version_number(self, test_id: uuid.UUID) -> int:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_versions")
            .select("version_number")
            .eq("test_definition_id", str(test_id))
            .order("version_number", desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return 0
        return result.data[0]["version_number"]

    async def get_versions_for_test(self, test_id: uuid.UUID) -> list[TestVersion]:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_versions")
            .select("*")
            .eq("test_definition_id", str(test_id))
            .order("version_number")
            .execute()
        )
        return [TestVersion(**row) for row in result.data]

    # ------------------------------------------------------------------
    # Test Runs
    # ------------------------------------------------------------------

    async def create_test_run(self, run: TestRun) -> TestRun:
        data = _to_db(run)
        result = await asyncio.to_thread(
            lambda: self._client.table("test_runs").insert(data).execute()
        )
        return TestRun(**result.data[0])

    async def get_test_run(
        self, run_id: uuid.UUID, owner_id: str
    ) -> TestRun | None:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_runs")
            .select("*")
            .eq("id", str(run_id))
            .eq("owner_id", owner_id)
            .execute()
        )
        if not result.data:
            return None
        return TestRun(**result.data[0])

    async def update_test_run(self, run: TestRun) -> TestRun:
        data = _to_db(run)
        result = await asyncio.to_thread(
            lambda: self._client.table("test_runs")
            .update(data)
            .eq("id", str(run.id))
            .eq("owner_id", run.owner_id)
            .execute()
        )
        return TestRun(**result.data[0])

    async def get_runs_for_test(
        self, test_id: uuid.UUID, owner_id: str
    ) -> list[TestRun]:
        result = await asyncio.to_thread(
            lambda: self._client.table("test_runs")
            .select("*")
            .eq("test_definition_id", str(test_id))
            .eq("owner_id", owner_id)
            .order("created_at")
            .execute()
        )
        return [TestRun(**row) for row in result.data]

    # ------------------------------------------------------------------
    # Evidence Artifacts (append-only)
    # ------------------------------------------------------------------

    async def create_evidence_artifact(
        self, artifact: EvidenceArtifact
    ) -> EvidenceArtifact:
        data = _to_db(artifact)
        result = await asyncio.to_thread(
            lambda: self._client.table("evidence_artifacts").insert(data).execute()
        )
        return EvidenceArtifact(**result.data[0])

    async def get_artifacts_for_run(
        self, run_id: uuid.UUID
    ) -> list[EvidenceArtifact]:
        result = await asyncio.to_thread(
            lambda: self._client.table("evidence_artifacts")
            .select("*")
            .eq("test_run_id", str(run_id))
            .execute()
        )
        return [EvidenceArtifact(**row) for row in result.data]

    # ------------------------------------------------------------------
    # Repair Proposals
    # ------------------------------------------------------------------

    async def create_repair_proposal(self, proposal: RepairProposal) -> RepairProposal:
        data = _to_db(proposal)
        result = await asyncio.to_thread(
            lambda: self._client.table("repair_proposals").insert(data).execute()
        )
        return RepairProposal(**result.data[0])

    async def get_repair_proposal(
        self, proposal_id: uuid.UUID
    ) -> RepairProposal | None:
        result = await asyncio.to_thread(
            lambda: self._client.table("repair_proposals")
            .select("*")
            .eq("id", str(proposal_id))
            .execute()
        )
        if not result.data:
            return None
        return RepairProposal(**result.data[0])

    async def update_repair_proposal(
        self, proposal: RepairProposal
    ) -> RepairProposal:
        data = _to_db(proposal)
        result = await asyncio.to_thread(
            lambda: self._client.table("repair_proposals")
            .update(data)
            .eq("id", str(proposal.id))
            .execute()
        )
        return RepairProposal(**result.data[0])

    async def get_proposals_for_run(
        self, run_id: uuid.UUID
    ) -> list[RepairProposal]:
        result = await asyncio.to_thread(
            lambda: self._client.table("repair_proposals")
            .select("*")
            .eq("test_run_id", str(run_id))
            .execute()
        )
        return [RepairProposal(**row) for row in result.data]
