"""Safe ZIP extraction service.

Security guarantees:
- Rejects archives exceeding configured size limits
- Prevents path traversal attacks (e.g. ../../etc/passwd)
- Rejects symlinks and absolute paths
- Detects ZIP bombs via per-entry and cumulative compression-ratio check
- Limits total file count
- Blocks secret-carrying extensions (.env, .pem, .key, etc.)
- Final path resolution check as belt-and-suspenders
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path, PurePosixPath

# Defaults — overridden by callers that pass settings
DEFAULT_MAX_ZIP_BYTES = 100 * 1024 * 1024   # 100 MB raw archive
DEFAULT_MAX_EXTRACTED = 200 * 1024 * 1024   # 200 MB extracted total
DEFAULT_MAX_FILES = 5_000
MAX_COMPRESSION_RATIO = 100                  # 100× expanded is suspicious
BLOCKED_SUFFIXES = frozenset({".env", ".pem", ".key", ".p12", ".pfx", ".cer", ".crt"})


class ZipValidationError(ValueError):
    """Raised when a ZIP archive fails a safety check."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_safe_member(name: str) -> bool:
    """Return True if the archive member path is safe to extract."""
    # Reject absolute paths on any OS
    if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
        return False
    # Reject path traversal
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return ".." not in parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """Detect symlinks via Unix external attributes."""
    # Top 16 bits of external_attr hold Unix permission bits
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    # 0xA000 is the stat.S_IFLNK constant
    return (unix_mode & 0xF000) == 0xA000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_and_extract(
    zip_bytes: bytes,
    dest: Path,
    *,
    max_zip_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED,
    max_files: int = DEFAULT_MAX_FILES,
) -> None:
    """Validate and safely extract a ZIP archive to *dest*.

    Raises :class:`ZipValidationError` for any safety violation.
    *dest* must already exist.
    """
    if len(zip_bytes) > max_zip_bytes:
        raise ZipValidationError(
            f"Archive size {len(zip_bytes) // 1024 // 1024} MB exceeds "
            f"limit of {max_zip_bytes // 1024 // 1024} MB"
        )

    try:
        bio = io.BytesIO(zip_bytes)
        zf = zipfile.ZipFile(bio, "r")
    except zipfile.BadZipFile as exc:
        raise ZipValidationError(f"Invalid or corrupt ZIP archive: {exc}") from exc

    with zf:
        members = zf.infolist()

        if len(members) > max_files:
            raise ZipValidationError(
                f"Archive contains {len(members)} entries, exceeding limit of {max_files}"
            )

        total_extracted = 0

        for info in members:
            name = info.filename

            # Block symlinks
            if _is_symlink(info):
                raise ZipValidationError(f"Archive contains symlink: {name!r}")

            # Path safety
            if not _is_safe_member(name):
                raise ZipValidationError(f"Unsafe path in archive: {name!r}")

            # Block sensitive extensions and known secret filenames
            p = Path(name)
            # .env has no suffix (suffix=''), so also check the bare name
            suffix = p.suffix.lower()
            bare_name = p.name.lower()
            if suffix in BLOCKED_SUFFIXES or bare_name in BLOCKED_SUFFIXES:
                raise ZipValidationError(
                    f"Archive contains blocked file type {bare_name!r}: {name!r}"
                )

            # ZIP bomb: per-entry compression ratio
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ZipValidationError(
                        f"Suspicious compression ratio {ratio:.0f}× for {name!r} "
                        f"(limit is {MAX_COMPRESSION_RATIO}×)"
                    )

            # ZIP bomb: cumulative extracted size
            total_extracted += info.file_size
            if total_extracted > max_extracted_bytes:
                raise ZipValidationError(
                    f"Extracted content would exceed "
                    f"{max_extracted_bytes // 1024 // 1024} MB limit"
                )

            # Extract
            if info.is_dir():
                (dest / name).mkdir(parents=True, exist_ok=True)
            else:
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)

                # Belt-and-suspenders: resolved path must stay inside dest
                try:
                    target.resolve().relative_to(dest.resolve())
                except ValueError:
                    raise ZipValidationError(
                        f"Path escapes destination after resolution: {name!r}"
                    )

                target.write_bytes(zf.read(name))


def compute_directory_hash(path: Path) -> str:
    """Return a deterministic SHA-256 hex digest of all files under *path*.

    Includes relative paths in the hash so structural changes are detected.
    """
    h = hashlib.sha256()
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            rel = file_path.relative_to(path)
            h.update(str(rel).encode("utf-8"))
            h.update(file_path.read_bytes())
    return h.hexdigest()
