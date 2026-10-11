"""Test definition service — creation, versioning, and retrieval.

Business rules:
- original_intent and required_assertions are immutable after creation.
- Versions are append-only (version_number monotonically increases).
- active_version_id is updated when a new version is created.
- Repair proposals create new versions; they never modify existing ones.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.models.domain import (
    ActionPlan,
    TestDefinition,
    TestVersion,
)
from app.models.requests import CreateTestRequest
from app.persistence.repository import PersistenceRepository

logger = logging.getLogger(__name__)


class TestDefinitionService:
    def __init__(self, repo: PersistenceRepository) -> None:
        self._repo = repo

    async def create_test(
        self,
        project_id: uuid.UUID,
        owner_id: str,
        request: CreateTestRequest,
    ) -> tuple[TestDefinition, TestVersion | None]:
        """Create a test definition. If an action_plan is supplied, also creates version 1."""
        # 1. Verify project exists and belongs to this owner
        project = await self._repo.get_project(project_id, owner_id)
        if not project:
            raise HTTPException(
                status_code=404,
                detail=f"Project {project_id} not found or access denied",
            )

        # 2. Build the definition (intent + assertions are set once here, never again)
        test_def = TestDefinition(
            project_id=project_id,
            owner_id=owner_id,
            name=request.name,
            original_intent=request.original_intent,
            required_assertions=request.required_assertions,
            preconditions=request.preconditions,
        )
        test_def = await self._repo.create_test_definition(test_def)
        logger.info(
            "Created test definition",
            extra={"test_id": str(test_def.id), "owner_id": owner_id},
        )

        # 3. Optionally create initial version
        version: TestVersion | None = None
        if request.action_plan:
            version = await self._create_version_internal(
                test_def=test_def,
                action_plan=request.action_plan,
                created_by=owner_id,
                change_reason="Initial version",
            )
            # Re-read so active_version_id is reflected in the returned object
            refreshed = await self._repo.get_test_definition(test_def.id, owner_id)
            if refreshed:
                test_def = refreshed

        return test_def, version

    async def get_test(
        self, test_id: uuid.UUID, owner_id: str
    ) -> tuple[TestDefinition, TestVersion | None]:
        """Fetch test definition and its active version."""
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )

        version: TestVersion | None = None
        if test_def.active_version_id:
            version = await self._repo.get_test_version(test_def.active_version_id)

        return test_def, version

    async def list_tests(
        self, project_id: uuid.UUID, owner_id: str
    ) -> list[tuple[TestDefinition, TestVersion | None]]:
        """Fetch all test definitions for a project with their active versions."""
        project = await self._repo.get_project(project_id, owner_id)
        if not project:
            raise HTTPException(
                status_code=404,
                detail=f"Project {project_id} not found or access denied",
            )
        definitions = await self._repo.list_test_definitions(project_id, owner_id)
        results: list[tuple[TestDefinition, TestVersion | None]] = []
        for td in definitions:
            version: TestVersion | None = None
            if td.active_version_id:
                version = await self._repo.get_test_version(td.active_version_id)
            results.append((td, version))
        return results

    async def create_version(
        self,
        test_id: uuid.UUID,
        owner_id: str,
        action_plan: ActionPlan,
        change_reason: str,
        repository_revision: str | None = None,
    ) -> TestVersion:
        """Append a new version to an existing test definition."""
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )

        version = await self._create_version_internal(
            test_def=test_def,
            action_plan=action_plan,
            created_by=owner_id,
            change_reason=change_reason,
            repository_revision=repository_revision,
        )
        return version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_version_internal(
        self,
        test_def: TestDefinition,
        action_plan: ActionPlan,
        created_by: str,
        change_reason: str,
        repository_revision: str | None = None,
    ) -> TestVersion:
        next_number = (await self._repo.get_latest_version_number(test_def.id)) + 1

        # Snapshot assertions at version creation time
        # This is what gets compared during repair verification
        version = TestVersion(
            test_definition_id=test_def.id,
            version_number=next_number,
            action_plan=action_plan,
            assertions_snapshot=test_def.required_assertions,  # Immutable source of truth
            repository_revision=repository_revision,
            created_by=created_by,
            change_reason=change_reason,
        )
        version = await self._repo.create_test_version(version)

        # Update active_version_id on the definition
        await self._repo.update_active_version(test_def.id, version.id, created_by)
        logger.info(
            "Created test version",
            extra={
                "test_id": str(test_def.id),
                "version_id": str(version.id),
                "version_number": next_number,
            },
        )
        return version
