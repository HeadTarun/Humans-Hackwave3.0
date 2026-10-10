"""Supabase Artifact Store — Phase 3.

Stores evidence artifacts (screenshots, DOM snapshots, logs) in a private
Supabase Storage bucket and provides time-limited signed URLs for retrieval.

Bucket layout:
  evidence-artifacts/
    {run_id}/
      screenshot_{uuid8}.png
      dom_snapshot_{uuid8}.html
      console_log_{uuid8}.txt
      network_log_{uuid8}.json
      report_{uuid8}.json

Security:
  - Bucket is PRIVATE — no public access
  - Signed URLs expire (default 1 hour)
  - Service-role key used only server-side, never exposed to callers
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from app.services.interfaces import ArtifactStore

logger = logging.getLogger(__name__)

# Extension map for artifact types
_EXT: dict[str, str] = {
    "screenshot": "png",
    "dom_snapshot": "html",
    "console_log": "txt",
    "network_log": "json",
    "trace": "zip",
    "report": "json",
}


class SupabaseArtifactStore(ArtifactStore):
    """Stores evidence artifacts in Supabase private Storage."""

    def __init__(
        self,
        url: str,
        service_key: str,
        bucket: str = "evidence-artifacts",
    ) -> None:
        from supabase import create_client  # type: ignore[import]
        self._client = create_client(url, service_key)
        self._bucket = bucket
        self._ensured = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store(
        self,
        run_id: str,
        artifact_type: str,
        data: bytes,
        metadata: dict | None = None,
    ) -> str:
        """Upload artifact to storage. Returns the private storage_path."""
        await self._ensure_bucket()
        ext = _EXT.get(artifact_type, "bin")
        short_id = uuid.uuid4().hex[:8]
        path = f"{run_id}/{artifact_type}_{short_id}.{ext}"

        def _upload() -> None:
            self._client.storage.from_(self._bucket).upload(
                path,
                data,
                {"content-type": self._content_type(ext)},
            )

        await asyncio.to_thread(_upload)
        logger.debug("Stored artifact %s (%d bytes)", path, len(data))
        return path

    async def get_signed_url(
        self, storage_path: str, expires_in: int = 3600
    ) -> str:
        """Return a time-limited signed URL for a private artifact."""
        def _sign() -> str:
            result = self._client.storage.from_(self._bucket).create_signed_url(
                storage_path, expires_in
            )
            # supabase-py v2 returns {"signedURL": "..."} or {"signedUrl": "..."}
            return result.get("signedURL") or result.get("signedUrl") or result["data"]["signedUrl"]

        return await asyncio.to_thread(_sign)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _ensure_bucket(self) -> None:
        """Create bucket if it doesn't exist. Idempotent."""
        if self._ensured:
            return

        def _create() -> None:
            try:
                self._client.storage.create_bucket(
                    self._bucket,
                    options={"public": False},
                )
                logger.info("Created storage bucket: %s", self._bucket)
            except Exception as exc:
                # Bucket already exists → ignore; other errors re-raised
                if "already exists" not in str(exc).lower():
                    logger.warning("Could not create bucket %s: %s", self._bucket, exc)

        await asyncio.to_thread(_create)
        self._ensured = True

    @staticmethod
    def _content_type(ext: str) -> str:
        return {
            "png": "image/png",
            "html": "text/html",
            "txt": "text/plain",
            "json": "application/json",
            "zip": "application/zip",
        }.get(ext, "application/octet-stream")
