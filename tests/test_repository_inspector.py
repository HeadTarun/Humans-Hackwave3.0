"""Tests for the Tree-sitter repository inspector and stack detection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.repository_inspector import TreeSitterInspector, detect_stack


# ---------------------------------------------------------------------------
# Helpers: build tiny fake repos in temp dirs
# ---------------------------------------------------------------------------

def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Stack detection (no tree-sitter required)
# ---------------------------------------------------------------------------

class TestStackDetection:
    def test_detects_nextjs(self, tmp_path):
        pkg = {"dependencies": {"next": "14.0.0", "react": "18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        s = detect_stack(tmp_path)
        assert s["name"] == "nextjs"
        assert s["version"] == "14.0.0"

    def test_detects_react(self, tmp_path):
        pkg = {"dependencies": {"react": "18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        s = detect_stack(tmp_path)
        assert s["name"] == "react"

    def test_detects_express(self, tmp_path):
        pkg = {"dependencies": {"express": "4.18.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        s = detect_stack(tmp_path)
        assert s["name"] == "express"

    def test_detects_python_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        s = detect_stack(tmp_path)
        assert s["name"] == "python"

    def test_detects_python_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\n')
        s = detect_stack(tmp_path)
        assert s["name"] == "python"

    def test_detects_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/app\n")
        s = detect_stack(tmp_path)
        assert s["name"] == "go"

    def test_detects_app_router_feature(self, tmp_path):
        pkg = {"dependencies": {"next": "14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "app").mkdir()
        s = detect_stack(tmp_path)
        assert "app_router" in s["features"]

    def test_detects_pages_router_feature(self, tmp_path):
        pkg = {"dependencies": {"next": "14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "pages").mkdir()
        s = detect_stack(tmp_path)
        assert "pages_router" in s["features"]

    def test_unknown_stack_for_empty_dir(self, tmp_path):
        s = detect_stack(tmp_path)
        assert s["name"] == "unknown"

    def test_detects_package_manager_yarn(self, tmp_path):
        pkg = {"dependencies": {"next": "14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "yarn.lock").write_text("")
        s = detect_stack(tmp_path)
        assert s["package_manager"] == "yarn"

    def test_detects_package_manager_pnpm(self, tmp_path):
        pkg = {"dependencies": {"next": "14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "pnpm-lock.yaml").write_text("")
        s = detect_stack(tmp_path)
        assert s["package_manager"] == "pnpm"


# ---------------------------------------------------------------------------
# TreeSitterInspector (structural and, if tree-sitter installed, parsed)
# ---------------------------------------------------------------------------

class TestTreeSitterInspector:
    @pytest.fixture
    def inspector(self):
        return TreeSitterInspector()

    @pytest.fixture
    def nextjs_repo(self, tmp_path):
        """A minimal fake Next.js repository."""
        pkg = {"dependencies": {"next": "14.0.0", "react": "18.0.0"}}
        write_files(tmp_path, {
            "package.json": json.dumps(pkg),
            "pages/index.js": (
                'export default function Home() {\n'
                '  return <div data-testid="home-page">Hello</div>;\n'
                '}\n'
            ),
            "pages/api/checkout.js": (
                'export default async function handler(req, res) {\n'
                '  res.json({ status: "ok" });\n'
                '}\n'
            ),
            "components/Button.jsx": (
                'export function Button({ label }) {\n'
                '  return <button aria-label={label}>{label}</button>;\n'
                '}\n'
            ),
            "node_modules/react/index.js": "// should be excluded",
        })
        return tmp_path

    @pytest.mark.asyncio
    async def test_inspect_returns_dict(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        assert isinstance(result, dict)
        assert "stack" in result
        assert "routes" in result
        assert "components" in result
        assert "api_handlers" in result
        assert "relevant_files" in result

    @pytest.mark.asyncio
    async def test_detects_nextjs_stack(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        assert result["stack"]["name"] == "nextjs"

    @pytest.mark.asyncio
    async def test_excludes_node_modules(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        for f in result["relevant_files"]:
            assert "node_modules" not in f

    @pytest.mark.asyncio
    async def test_classifies_pages_as_routes(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        route_files = [r["file"] for r in result["routes"]]
        # pages/index.js should be a route (not api handler)
        assert any("index" in f for f in route_files)

    @pytest.mark.asyncio
    async def test_classifies_api_as_handlers(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        api_files = [h["file"] for h in result["api_handlers"]]
        assert any("checkout" in f for f in api_files)

    @pytest.mark.asyncio
    async def test_extracts_test_ids_when_ts_available(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        if result["inspection_method"] == "tree_sitter":
            all_test_ids = []
            for section in ("routes", "components", "api_handlers"):
                for item in result[section]:
                    all_test_ids.extend(item.get("test_ids", []))
            # home-page test id should be found
            assert any("home-page" in tid for tid in all_test_ids)

    @pytest.mark.asyncio
    async def test_extracts_exports_when_ts_available(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        if result["inspection_method"] == "tree_sitter":
            all_exports = []
            for section in ("routes", "components", "api_handlers"):
                for item in result[section]:
                    all_exports.extend(item.get("exports", []))
            assert len(all_exports) > 0

    @pytest.mark.asyncio
    async def test_inspect_empty_dir(self, inspector, tmp_path):
        result = await inspector.inspect(str(tmp_path))
        assert result["routes"] == []
        assert result["components"] == []
        assert result["api_handlers"] == []

    @pytest.mark.asyncio
    async def test_snippets_are_redacted(self, inspector, tmp_path):
        """Secret-like values in source must not appear in snippet."""
        write_files(tmp_path, {
            "package.json": json.dumps({"dependencies": {"next": "14.0.0"}}),
            "pages/index.js": 'const apiKey = "sk-abc123secretABC456defGHI789jkl";\n',
        })
        result = await inspector.inspect(str(tmp_path))
        for section in ("routes", "components", "api_handlers"):
            for item in result[section]:
                assert "sk-abc123" not in item.get("snippet", "")

    @pytest.mark.asyncio
    async def test_bundle_has_inspection_method(self, inspector, nextjs_repo):
        result = await inspector.inspect(str(nextjs_repo))
        assert result["inspection_method"] in ("tree_sitter", "structural")
