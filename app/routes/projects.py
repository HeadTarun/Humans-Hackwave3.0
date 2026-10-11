"""Project routes — POST /projects, GET /projects/{id}."""
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_owner_id, get_repository
from app.models.domain import Project
from app.models.requests import CreateProjectRequest
from app.models.responses import ProjectListResponse, ProjectResponse
from app.persistence.repository import PersistenceRepository

router = APIRouter()


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> ProjectListResponse:
    projects = await repo.list_projects(owner_id)
    return ProjectListResponse(
        total=len(projects),
        projects=[ProjectResponse(**p.model_dump()) for p in projects],
    )


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> ProjectResponse:
    project = Project(
        owner_id=owner_id,
        name=body.name,
        repo_metadata=body.repo_metadata,
    )
    project = await repo.create_project(project)
    return ProjectResponse(**project.model_dump())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
) -> ProjectResponse:
    project = await repo.get_project(project_id, owner_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found or access denied")
    return ProjectResponse(**project.model_dump())
