"""Tests for the ZIP validation and safe extraction service."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.services.zip_service import (
    ZipValidationError,
    compute_directory_hash,
    validate_and_extract,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_zip(entries: dict[str, bytes]) -> bytes:
    """Build a valid ZIP from {filename: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_zip_raw_name(name: str, content: bytes = b"x") -> bytes:
    """Build a ZIP with a raw (potentially unsafe) member name."""
    buf = io.BytesIO()
    info = zipfile.ZipInfo(name)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(info, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Valid extraction
# ---------------------------------------------------------------------------

class TestValidExtraction:
    def test_extracts_simple_file(self, tmp_path):
        z = make_zip({"hello.txt": b"world"})
        validate_and_extract(z, tmp_path)
        assert (tmp_path / "hello.txt").read_bytes() == b"world"

    def test_extracts_nested_directories(self, tmp_path):
        z = make_zip({"src/app/page.tsx": b"export default function Page() {}"})
        validate_and_extract(z, tmp_path)
        assert (tmp_path / "src" / "app" / "page.tsx").exists()

    def test_extracts_multiple_files(self, tmp_path):
        files = {f"file_{i}.txt": f"content_{i}".encode() for i in range(10)}
        z = make_zip(files)
        validate_and_extract(z, tmp_path)
        for name in files:
            assert (tmp_path / name).exists()

    def test_empty_file_allowed(self, tmp_path):
        z = make_zip({"empty.js": b""})
        validate_and_extract(z, tmp_path)
        assert (tmp_path / "empty.js").read_bytes() == b""


# ---------------------------------------------------------------------------
# Security rejections
# ---------------------------------------------------------------------------

class TestSecurityRejections:
    def test_rejects_path_traversal_dotdot(self, tmp_path):
        z = make_zip_raw_name("../../etc/passwd", b"evil")
        with pytest.raises(ZipValidationError, match="Unsafe path"):
            validate_and_extract(z, tmp_path)

    def test_rejects_nested_path_traversal(self, tmp_path):
        z = make_zip_raw_name("src/../../../evil.txt", b"evil")
        with pytest.raises(ZipValidationError, match="Unsafe path"):
            validate_and_extract(z, tmp_path)

    def test_rejects_absolute_unix_path(self, tmp_path):
        z = make_zip_raw_name("/etc/passwd", b"evil")
        with pytest.raises(ZipValidationError, match="Unsafe path"):
            validate_and_extract(z, tmp_path)

    def test_rejects_blocked_env_extension(self, tmp_path):
        z = make_zip({".env": b"SECRET=hunter2"})
        with pytest.raises(ZipValidationError, match="blocked file type"):
            validate_and_extract(z, tmp_path)

    def test_rejects_blocked_pem_extension(self, tmp_path):
        z = make_zip({"cert.pem": b"-----BEGIN CERTIFICATE-----"})
        with pytest.raises(ZipValidationError, match="blocked file type"):
            validate_and_extract(z, tmp_path)

    def test_rejects_blocked_key_extension(self, tmp_path):
        z = make_zip({"private.key": b"-----BEGIN PRIVATE KEY-----"})
        with pytest.raises(ZipValidationError, match="blocked file type"):
            validate_and_extract(z, tmp_path)

    def test_rejects_archive_exceeding_size_limit(self, tmp_path):
        z = make_zip({"x.txt": b"a" * 100})
        with pytest.raises(ZipValidationError, match="exceeds"):
            validate_and_extract(z, tmp_path, max_zip_bytes=10)

    def test_rejects_too_many_files(self, tmp_path):
        files = {f"f{i}.txt": b"x" for i in range(10)}
        z = make_zip(files)
        with pytest.raises(ZipValidationError, match="exceeding limit"):
            validate_and_extract(z, tmp_path, max_files=5)

    def test_rejects_total_extracted_size_exceeded(self, tmp_path):
        files = {f"f{i}.txt": b"x" * 200 for i in range(5)}  # 1000 bytes total
        z = make_zip(files)
        with pytest.raises(ZipValidationError, match="exceed"):
            validate_and_extract(z, tmp_path, max_extracted_bytes=100)

    def test_rejects_invalid_zip_bytes(self, tmp_path):
        with pytest.raises(ZipValidationError, match="Invalid"):
            validate_and_extract(b"this is not a zip file", tmp_path)

    def test_rejects_empty_bytes(self, tmp_path):
        with pytest.raises(ZipValidationError):
            validate_and_extract(b"", tmp_path)

    def test_zip_bomb_high_ratio_rejected(self, tmp_path):
        """An entry that expands >100× its compressed size is rejected."""
        # null bytes compress extremely well
        payload = b"\x00" * 2_000_000
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr("bomb.bin", payload)
        z = buf.getvalue()

        zf_check = zipfile.ZipFile(io.BytesIO(z))
        info = zf_check.infolist()[0]
        if info.compress_size > 0 and (info.file_size / info.compress_size) > 100:
            with pytest.raises(ZipValidationError, match="ratio"):
                validate_and_extract(z, tmp_path)
        else:
            pytest.skip("Compression ratio not high enough on this platform")


# ---------------------------------------------------------------------------
# Directory hash
# ---------------------------------------------------------------------------

class TestDirectoryHash:
    def test_hash_is_hex_sha256(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        h = compute_directory_hash(tmp_path)
        assert len(h) == 64
        int(h, 16)  # must parse as hex

    def test_hash_is_deterministic(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        assert compute_directory_hash(tmp_path) == compute_directory_hash(tmp_path)

    def test_hash_changes_on_content_change(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        h1 = compute_directory_hash(tmp_path)
        (tmp_path / "a.txt").write_bytes(b"world")
        h2 = compute_directory_hash(tmp_path)
        assert h1 != h2

    def test_hash_changes_on_new_file(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        h1 = compute_directory_hash(tmp_path)
        (tmp_path / "b.txt").write_bytes(b"new")
        h2 = compute_directory_hash(tmp_path)
        assert h1 != h2

    def test_two_identical_dirs_have_same_hash(self, tmp_path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        for d in (d1, d2):
            (d / "main.js").write_bytes(b"console.log('hi')")
            (d / "README.md").write_bytes(b"# Hello")
        assert compute_directory_hash(d1) == compute_directory_hash(d2)
