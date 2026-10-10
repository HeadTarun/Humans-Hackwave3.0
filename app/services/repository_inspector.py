"""Tree-sitter repository inspector — Phase 2 real implementation.

Parses JS/TS source files (JavaScript grammar covers .js/.jsx/.ts/.tsx
for the demo Next.js stack) to extract:
  - Exported component / function names
  - Route file classifications (pages/, app router page.tsx, api/)
  - data-testid / aria-label attributes (locator context for repair)
  - Stack detection from config files

Returns a bounded, redacted code-context bundle.
Does NOT execute any repository code.

Assumption: tree-sitter-javascript grammar handles .ts/.tsx adequately
for extracting identifiers and JSX attributes. Full TypeScript-specific
grammar (generics, decorators) may be added in a later phase.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services.interfaces import RepositoryInspector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
EXCLUDED_DIRS = frozenset({
    "node_modules", ".next", "dist", "build", ".git",
    "__pycache__", "coverage", ".turbo", ".cache",
})

MAX_FILES_TO_INSPECT = 50
MAX_CHARS_PER_SNIPPET = 2_000
MAX_BUNDLE_CHARS = 60_000

# Patterns to redact before the bundle is stored or sent to Groq
_SECRET_RE = [
    re.compile(r'(?i)(api[_\-]?key|secret|password|token|auth)\s*[:=]\s*["\']([^"\']{6,})["\']'),
    re.compile(r'(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'sk-[A-Za-z0-9]{32,}'),
    re.compile(r'(?i)SUPABASE_SERVICE_ROLE[^\s]*'),
    re.compile(r'(?i)GROQ_API_KEY[^\s]*'),
]
_REDACTED = "[REDACTED]"


def _redact(code: str) -> str:
    for pat in _SECRET_RE:
        code = pat.sub(_REDACTED, code)
    return code


# ---------------------------------------------------------------------------
# Stack detection (no code execution)
# ---------------------------------------------------------------------------

def detect_stack(root: Path) -> dict[str, Any]:
    """Detect project stack from config files only. Never executes code."""
    result: dict[str, Any] = {"name": "unknown", "version": None, "features": []}

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            result["name"] = "node"
            if "next" in all_deps:
                result["name"] = "nextjs"
                result["version"] = all_deps["next"]
                # Detect app router vs pages router
                if (root / "app").is_dir():
                    result["features"].append("app_router")
                if (root / "pages").is_dir():
                    result["features"].append("pages_router")
            elif "react" in all_deps:
                result["name"] = "react"
            elif "express" in all_deps:
                result["name"] = "express"
            result["package_manager"] = "npm"
            if (root / "yarn.lock").exists():
                result["package_manager"] = "yarn"
            elif (root / "pnpm-lock.yaml").exists():
                result["package_manager"] = "pnpm"
        except Exception:
            result["name"] = "node"
        return result

    if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
        result["name"] = "python"
        return result

    if (root / "go.mod").exists():
        result["name"] = "go"
        return result

    if (root / "Cargo.toml").exists():
        result["name"] = "rust"
        return result

    return result


# ---------------------------------------------------------------------------
# Tree-sitter inspector
# ---------------------------------------------------------------------------

class TreeSitterInspector(RepositoryInspector):
    """Production RepositoryInspector using Tree-sitter.

    Gracefully degrades to structural inspection if tree-sitter is not
    installed (useful in environments where the C extension can't build).
    """

    def __init__(self) -> None:
        self._parser = None
        self._language = None
        self._ts_available = self._init_tree_sitter()

    def _init_tree_sitter(self) -> bool:
        try:
            from tree_sitter import Language, Parser  # type: ignore[import]
            import tree_sitter_javascript as tsjs    # type: ignore[import]
            self._language = Language(tsjs.language())
            self._parser = Parser(self._language)
            logger.info("Tree-sitter initialised with JavaScript grammar")
            return True
        except Exception as exc:
            logger.warning("Tree-sitter unavailable, using structural fallback: %s", exc)
            return False

    async def inspect(self, repo_path: str) -> dict[str, Any]:
        root = Path(repo_path)
        stack = detect_stack(root)

        result: dict[str, Any] = {
            "stack": stack,
            "repo_path": repo_path,
            "routes": [],
            "components": [],
            "api_handlers": [],
            "relevant_files": [],
            "inspection_method": "tree_sitter" if self._ts_available else "structural",
            "bundle_truncated": False,
        }

        files = self._collect_files(root)
        result["relevant_files"] = [str(f.relative_to(root)) for f in files]

        total_chars = 0
        for fpath in files:
            if total_chars >= MAX_BUNDLE_CHARS:
                result["bundle_truncated"] = True
                break
            info = self._inspect_file(root, fpath)
            if not info:
                continue
            total_chars += len(info.get("snippet", ""))
            rel = str(fpath.relative_to(root))
            if _is_api_file(rel):
                result["api_handlers"].append(info)
            elif _is_route_file(rel):
                result["routes"].append(info)
            else:
                result["components"].append(info)

        return result

    def _collect_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for ext in SUPPORTED_EXTENSIONS:
            for f in root.rglob(f"*{ext}"):
                if any(p in EXCLUDED_DIRS for p in f.parts):
                    continue
                files.append(f)
                if len(files) >= MAX_FILES_TO_INSPECT:
                    break
        # Prioritise route/page files for the demo stack
        def priority(p: Path) -> int:
            s = str(p)
            if "pages" in s or "app" in s:
                return 0
            if "components" in s:
                return 1
            return 2
        return sorted(files, key=priority)[:MAX_FILES_TO_INSPECT]

    def _inspect_file(self, root: Path, fpath: Path) -> dict[str, Any] | None:
        try:
            source = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not source.strip():
            return None

        exports: list[str] = []
        test_ids: list[str] = []
        button_texts: list[str] = []
        link_texts: list[str] = []

        if self._ts_available and self._parser and self._language:
            try:
                tree = self._parser.parse(source.encode("utf-8", errors="replace"))
                exports = self._extract_exports(tree.root_node, source)
                test_ids = self._extract_locator_attrs(tree.root_node, source)
                # Gap 4: Extract button and link text — most common LOCATOR_DRIFT target
                button_texts, link_texts = self._extract_button_link_texts(
                    tree.root_node, source
                )
            except Exception as exc:
                logger.debug("Tree-sitter parse error for %s: %s", fpath, exc)

        snippet = _redact(source[:MAX_CHARS_PER_SNIPPET])

        return {
            "file": str(fpath.relative_to(root)),
            "exports": exports,
            "test_ids": test_ids[:20],
            "button_texts": button_texts[:20],   # Gap 4: button label text
            "link_texts": link_texts[:20],        # Gap 4: anchor label text
            "snippet": snippet,
        }

    def _extract_exports(self, root_node: Any, source: str) -> list[str]:
        """Extract exported identifiers via tree-sitter query."""
        if not self._language:
            return []
        try:
            # All export forms use export_statement in tree-sitter-javascript 0.23.x
            query = self._language.query("""
                (export_statement
                  (function_declaration
                    name: (identifier) @name))
                (export_statement
                  (lexical_declaration
                    (variable_declarator name: (identifier) @name)))
            """)
            captures: dict[str, list] = query.captures(root_node)
            names: list[str] = []
            for node_list in captures.values():
                for node in node_list:
                    text = source[node.start_byte:node.end_byte]
                    if len(text) < 100:
                        names.append(text)
            return list(dict.fromkeys(names))[:20]
        except Exception as exc:
            logger.debug("Export query error: %s", exc)
            return []

    def _extract_locator_attrs(self, root_node: Any, source: str) -> list[str]:
        """Extract data-testid / aria-label / role attrs for locator context.

        In tree-sitter-javascript 0.23.x JSX attribute names are
        'property_identifier' nodes (not 'jsx_attribute_name').
        """
        if not self._language:
            return []
        try:
            # Capture the whole jsx_attribute then pull children manually
            query = self._language.query("(jsx_attribute) @attr")
            captures: dict[str, list] = query.captures(root_node)
            INTERESTING = {"data-testid", "aria-label", "id", "role", "name", "type"}
            results: list[str] = []
            for node_list in captures.values():
                for attr_node in node_list:
                    children = attr_node.children
                    # children: [property_identifier, '=', string]
                    if len(children) < 3:
                        continue
                    name_node = children[0]
                    val_node = children[2]
                    attr_name = source[name_node.start_byte:name_node.end_byte]
                    if attr_name not in INTERESTING:
                        continue
                    # Drill into string → string_fragment
                    if val_node.type == "string" and val_node.children:
                        for frag in val_node.children:
                            if frag.type == "string_fragment":
                                val = source[frag.start_byte:frag.end_byte]
                                results.append(f'{attr_name}="{val}"')
                                break
            return results[:20]
        except Exception as exc:
            logger.debug("Locator attr query error: %s", exc)
            return []

    def _extract_button_link_texts(
        self, root_node: Any, source: str
    ) -> tuple[list[str], list[str]]:
        """Gap 4: Extract text content of <button> and <a> JSX elements.

        This is the single most valuable data for LOCATOR_DRIFT repair:
        when a button label changes from 'Checkout' to 'Complete Order',
        Tree-sitter tells us the new label from source WITHOUT running the app.

        Uses text-walking (no query API) for maximum compatibility with
        tree-sitter-javascript 0.23.x node types.
        """
        button_texts: list[str] = []
        link_texts: list[str] = []

        def _collect_jsx_text(node: Any) -> str:
            """Recursively collect all jsx_text leaf content under a node."""
            parts: list[str] = []
            if node.type == "jsx_text":
                parts.append(source[node.start_byte:node.end_byte].strip())
            for child in (node.children or []):
                parts.append(_collect_jsx_text(child))
            return " ".join(p for p in parts if p).strip()

        def _walk(node: Any) -> None:
            if node.type in ("jsx_element", "jsx_self_closing_element"):
                # Check the opening tag name
                open_tag = None
                for child in (node.children or []):
                    if child.type == "jsx_opening_element":
                        open_tag = child
                        break
                    if child.type == "jsx_self_closing_element":
                        open_tag = child
                        break

                if open_tag is not None:
                    # Get tag name from first child that is identifier/member_expression
                    tag_name = ""
                    for c in (open_tag.children or []):
                        if c.type in ("identifier", "member_expression"):
                            tag_name = source[c.start_byte:c.end_byte].lower()
                            break

                    if tag_name == "button":
                        text = _collect_jsx_text(node)
                        if text and len(text) < 80:
                            button_texts.append(text)
                    elif tag_name == "a":
                        text = _collect_jsx_text(node)
                        if text and len(text) < 80:
                            link_texts.append(text)

            for child in (node.children or []):
                if len(button_texts) + len(link_texts) < 40:  # bounded
                    _walk(child)

        try:
            _walk(root_node)
        except Exception as exc:
            logger.debug("Button/link text extraction error: %s", exc)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_buttons = []
        for t in button_texts:
            if t not in seen:
                seen.add(t)
                unique_buttons.append(t)

        seen_links: set[str] = set()
        unique_links = []
        for t in link_texts:
            if t not in seen_links:
                seen_links.add(t)
                unique_links.append(t)

        return unique_buttons[:20], unique_links[:20]


def _is_route_file(rel: str) -> bool:
    parts = Path(rel).parts
    last = parts[-1] if parts else ""
    return (
        "pages" in parts
        or last in {"page.tsx", "page.ts", "page.jsx", "page.js",
                    "layout.tsx", "layout.ts"}
    )


def _is_api_file(rel: str) -> bool:
    parts = Path(rel).parts
    return (
        ("api" in parts and "pages" in parts)  # pages/api/...
        or ("route.ts" in parts or "route.js" in parts)  # app router API
    )
