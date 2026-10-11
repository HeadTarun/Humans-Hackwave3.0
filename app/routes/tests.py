"""Test definition routes."""
import uuid

from fastapi import APIRouter, Depends

from app.dependencies import get_owner_id, get_repository
from app.models.requests import CreateTestRequest
from app.models.responses import (
    TestDefinitionListResponse,
    TestDefinitionResponse,
    TestVersionResponse,
)
from app.persistence.repository import PersistenceRepository
from app.services.test_definition_service import TestDefinitionService

router = APIRouter()


def _make_service(repo: PersistenceRepository) -> TestDefinitionService:
    return TestDefinitionService(repo)


@router.get(
    "/projects/{project_id}/tests",
    response_model=TestDefinitionListResponse,
    tags=["tests"],
)
async def list_tests(
    project_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> TestDefinitionListResponse:
    svc = _make_service(repo)
    pairs = await svc.list_tests(project_id, owner_id)
    tests = [
        TestDefinitionResponse(
            **td.model_dump(),
            current_version=TestVersionResponse(**v.model_dump()) if v else None,
        )
        for td, v in pairs
    ]
    return TestDefinitionListResponse(
        project_id=project_id,
        total=len(tests),
        tests=tests,
    )


@router.post(
    "/projects/{project_id}/tests",
    response_model=TestDefinitionResponse,
    status_code=201,
    tags=["tests"],
)
async def create_test(
    project_id: uuid.UUID,
    body: CreateTestRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> TestDefinitionResponse:
    svc = _make_service(repo)
    test_def, version = await svc.create_test(project_id, owner_id, body)

    current_version = None
    if version:
        current_version = TestVersionResponse(**version.model_dump())

    return TestDefinitionResponse(
        **test_def.model_dump(),
        current_version=current_version,
    )


@router.get(
    "/tests/{test_id}",
    response_model=TestDefinitionResponse,
    tags=["tests"],
)
async def get_test(
    test_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> TestDefinitionResponse:
    svc = _make_service(repo)
    test_def, version = await svc.get_test(test_id, owner_id)

    current_version = None
    if version:
        current_version = TestVersionResponse(**version.model_dump())

    return TestDefinitionResponse(
        **test_def.model_dump(),
        current_version=current_version,
    )
