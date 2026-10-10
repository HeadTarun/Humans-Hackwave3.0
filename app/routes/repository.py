"""Repository upload route — POST /projects/{project_id}/repository

Accepts a ZIP file, validates it for safety, extracts it to a temporary
directory, inspects the code with Tree-sitter, computes a content hash,
and stores the metadata on the project.

Security:
- Size, file-count, path-traversal, symlink, zip-bomb checks in zip_service
- Temp directory cleaned up in a finally block
- Repository contents treated as untrusted input
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.dependencies import get_owner_id, get_repository
from app.persistence.repository import PersistenceRepository
from app.services.repository_inspector import TreeSitterInspector
from app.services.zip_service import ZipValidationError, compute_directory_hash, validate_and_extract

logger = logging.getLogger(__name__)
router = APIRouter()

# Singleton inspector (initialises tree-sitter once)
_inspector = TreeSitterInspector()


@router.post(
    "/{project_id}/repository",
    status_code=200,
    tags=["repository"],
    summary="Upload a repository ZIP for inspection",
)
async def upload_repository(
    project_id: uuid.UUID,
    file: UploadFile,
    owner_id: str = Depends(get_owner_id),
    repo: PersistenceRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Upload a repository ZIP. Returns stack detection and inspection summary.

    The ZIP is:
    1. Validated (size, path traversal, zip bomb, symlinks)
    2. Extracted to a temp directory
    3. Inspected with Tree-sitter (JS/TS routes, components, test IDs)
    4. A content hash is computed and stored on the project
    """
    # Verify project ownership
    project = await repo.get_project(project_id, owner_id)
    if not project:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found or access denied",
        )

    # Read uploaded bytes
    zip_bytes = await file.read()
    if not zip_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    max_zip = settings.max_repo_size_mb * 1024 * 1024
    max_extracted = settings.max_extracted_size_mb * 1024 * 1024

    with tempfile.TemporaryDirectory(prefix="healtest_repo_") as tmp:
        tmp_path = Path(tmp)

        # 1. Validate and extract
        try:
            validate_and_extract(
                zip_bytes,
                tmp_path,
                max_zip_bytes=max_zip,
                max_extracted_bytes=max_extracted,
                max_files=settings.max_repo_files,
            )
        except ZipValidationError as exc:
            raise HTTPException(status_code=422, detail=f"ZIP validation failed: {exc}")

        # 2. Compute content hash
        repo_hash = compute_directory_hash(tmp_path)

        # 3. Inspect with Tree-sitter
        inspection = await _inspector.inspect(str(tmp_path))

    # 4. Store hash + inspection summary on project.repo_metadata
    updated_metadata = {
        **(project.repo_metadata or {}),
        "repo_hash": repo_hash,
        "stack": inspection["stack"],
        "inspection_method": inspection["inspection_method"],
        "route_count": len(inspection["routes"]),
        "component_count": len(inspection["components"]),
        "api_handler_count": len(inspection["api_handlers"]),
        "relevant_files": inspection["relevant_files"][:20],
        "bundle_truncated": inspection.get("bundle_truncated", False),
    }

    # Persist updated metadata (update_active_version handles the mutation;
    # we update repo_metadata directly via a targeted update)
    existing = project.model_copy(update={"repo_metadata": updated_metadata})
    await repo.update_project_metadata(project_id, updated_metadata, owner_id)

    logger.info(
        "Repository uploaded and inspected",
        extra={
            "project_id": str(project_id),
            "repo_hash": repo_hash[:16],
            "stack": inspection["stack"].get("name"),
            "files_inspected": len(inspection["relevant_files"]),
        },
    )

    return {
        "project_id": str(project_id),
        "repo_hash": repo_hash,
        "stack": inspection["stack"],
        "inspection_summary": {
            "method": inspection["inspection_method"],
            "routes": inspection["routes"],
            "api_handlers": inspection["api_handlers"],
            "component_count": len(inspection["components"]),
            "relevant_files": inspection["relevant_files"],
            "bundle_truncated": inspection.get("bundle_truncated", False),
        },
    }
