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
            "ux_elements": [],
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
            if "ux_elements" in info:
                result["ux_elements"].extend(info["ux_elements"])
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
        ux_elements: list[dict[str, Any]] = []

        if self._ts_available and self._parser and self._language:
            try:
                tree = self._parser.parse(source.encode("utf-8", errors="replace"))
                exports = self._extract_exports(tree.root_node, source)
                test_ids = self._extract_locator_attrs(tree.root_node, source)
                # Gap 4: Extract button and link text — most common LOCATOR_DRIFT target
                button_texts, link_texts = self._extract_button_link_texts(
                    tree.root_node, source
                )
                # UX False Positive Context: handlers, ARIA, state guards, wrappers
                rel_file = str(fpath.relative_to(root))
                ux_elements = self._extract_ux_elements(tree.root_node, source, rel_file)
            except Exception as exc:
                logger.debug("Tree-sitter parse error for %s: %s", fpath, exc)

        snippet = _redact(source[:MAX_CHARS_PER_SNIPPET])

        return {
            "file": str(fpath.relative_to(root)),
            "exports": exports,
            "test_ids": test_ids[:20],
            "button_texts": button_texts[:20],   # Gap 4: button label text
            "link_texts": link_texts[:20],        # Gap 4: anchor label text
            "ux_elements": ux_elements[:50],      # UX false positive grounding
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

    def _extract_ux_elements(
        self, root_node: Any, source: str, file_rel: str
    ) -> list[dict[str, Any]]:
        """Extract rich UX interactive elements for false positive diagnosis.

        Captures:
          - tag name (button, a, input, Button, IconButton, Link, DialogTrigger)
          - inner visible text
          - event handlers (onClick, onSubmit, etc.)
          - state guards (disabled, aria-disabled)
          - selectors (data-testid, id, aria-label, role)
          - parent containers (Dialog, Modal, Form, Drawer)
          - exact file & line number
        """
        INTERACTIVE_TAGS = {
            "button", "a", "input", "select", "textarea",
            "Button", "IconButton", "Link", "DialogTrigger",
            "Pressable", "SubmitButton", "MenuItem", "ActionIcon",
        }
        CONTAINER_TAGS = {
            "Dialog", "DialogContent", "Modal", "Drawer", "Sheet",
            "Form", "Card", "Accordion", "Popover", "AlertDialog",
        }

        ux_elements: list[dict[str, Any]] = []

        def _get_tag_name(open_node: Any) -> str:
            for c in (open_node.children or []):
                if c.type in ("identifier", "member_expression", "nested_identifier"):
                    return source[c.start_byte:c.end_byte]
            return ""

        def _collect_jsx_text(elem_node: Any) -> str:
            parts: list[str] = []
            for child in (elem_node.children or []):
                # Never traverse opening or closing tags or attributes
                if child.type in ("jsx_opening_element", "jsx_closing_element", "jsx_attribute"):
                    continue
                if child.type == "jsx_text":
                    txt = source[child.start_byte:child.end_byte].strip()
                    if txt:
                        parts.append(txt)
                elif child.type == "jsx_element":
                    sub_txt = _collect_jsx_text(child)
                    if sub_txt:
                        parts.append(sub_txt)
                elif child.type == "jsx_expression":
                    for expr_c in (child.children or []):
                        if expr_c.type == "string":
                            parts.append(source[expr_c.start_byte:expr_c.end_byte].strip("\"'"))
                        elif expr_c.type == "identifier":
                            parts.append(f"${{{source[expr_c.start_byte:expr_c.end_byte]}}}")
            return " ".join(parts).strip()

        def _parse_attributes(open_node: Any) -> dict[str, Any]:
            attrs: dict[str, Any] = {}
            for child in (open_node.children or []):
                if child.type == "jsx_attribute":
                    c_list = child.children or []
                    if not c_list:
                        continue
                    attr_name = source[c_list[0].start_byte:c_list[0].end_byte]
                    if len(c_list) == 1:
                        attrs[attr_name] = True
                    elif len(c_list) >= 3:
                        val_node = c_list[2]
                        if val_node.type == "string":
                            val_str = source[val_node.start_byte:val_node.end_byte].strip("\"'")
                            attrs[attr_name] = val_str
                        elif val_node.type == "jsx_expression":
                            expr_str = source[val_node.start_byte:val_node.end_byte].strip("{} ")
                            attrs[attr_name] = expr_str
            return attrs

        def _walk(node: Any, containers: list[str]) -> None:
            if len(ux_elements) >= 50:
                return

            if node.type in ("jsx_element", "jsx_self_closing_element"):
                open_node = None
                if node.type == "jsx_self_closing_element":
                    open_node = node
                else:
                    for child in (node.children or []):
                        if child.type == "jsx_opening_element":
                            open_node = child
                            break

                if open_node is not None:
                    tag = _get_tag_name(open_node)
                    is_container = tag in CONTAINER_TAGS or any(tag.endswith(ct) for ct in CONTAINER_TAGS)
                    curr_containers = containers + [tag] if is_container else containers

                    attrs = _parse_attributes(open_node)
                    has_handler = any(k.startswith("on") for k in attrs)
                    is_interactive = (
                        tag in INTERACTIVE_TAGS
                        or tag.lower() in INTERACTIVE_TAGS
                        or has_handler
                        or "data-testid" in attrs
                        or attrs.get("role") in ("button", "link", "tab")
                    )

                    if is_interactive:
                        line_no = node.start_point[0] + 1
                        inner_text = _collect_jsx_text(node) if node.type == "jsx_element" else ""

                        # Find primary handler
                        handler = next((f"{k}={{{attrs[k]}}}" for k in ("onClick", "onSubmit", "onPress", "onChange") if k in attrs), None)

                        # Find disabled guard
                        disabled_cond = None
                        if "disabled" in attrs:
                            disabled_cond = str(attrs["disabled"])
                        elif "aria-disabled" in attrs:
                            disabled_cond = str(attrs["aria-disabled"])

                        ux_elements.append({
                            "tag": tag,
                            "text": inner_text[:100],
                            "test_id": attrs.get("data-testid") or attrs.get("testId") or attrs.get("data-cy"),
                            "aria_label": attrs.get("aria-label"),
                            "handler": handler,
                            "disabled_guard": disabled_cond,
                            "parent_wrappers": list(containers),
                            "file": file_rel,
                            "line": line_no,
                        })

                    # Recurse children with updated container stack
                    for child in (node.children or []):
                        if child != open_node:
                            _walk(child, curr_containers)
                    return

            for child in (node.children or []):
                _walk(child, containers)

        try:
            _walk(root_node, [])
        except Exception as exc:
            logger.debug("UX elements extraction error: %s", exc)

        return ux_elements


def format_llm_ux_context(
    inspection_result: dict[str, Any],
    failed_target: dict[str, Any] | None = None,
) -> str:
    """Format extracted Tree-Sitter UX elements into an LLM grounding block."""
    ux_elements = inspection_result.get("ux_elements", [])
    if not ux_elements:
        return "No interactive UX elements indexed in codebase."

    lines = [
        "### [Repository UX Semantic Context (Tree-Sitter AST Analysis)]",
        "The following interactive components and event handlers exist in the current codebase:",
    ]

    for el in ux_elements[:15]:
        tag = el.get("tag", "element")
        file_loc = f"{el.get('file', 'unknown')}:{el.get('line', '?')}"
        details = []
        if el.get("text"):
            details.append(f'Text: "{el["text"]}"')
        if el.get("handler"):
            details.append(f'Handler: `{el["handler"]}`')
        if el.get("test_id"):
            details.append(f'TestId: `data-testid="{el["test_id"]}"`')
        if el.get("aria_label"):
            details.append(f'AriaLabel: `aria-label="{el["aria_label"]}"`')
        if el.get("disabled_guard"):
            details.append(f'DisabledGuard: `disabled={{{el["disabled_guard"]}}}`')
        if el.get("parent_wrappers"):
            details.append(f'NestedInside: {el["parent_wrappers"]}')

        lines.append(f"- `<{tag}>` at `{file_loc}`: " + " | ".join(details))

    lines.append("")
    lines.append(
        "[LLM Guidance]: Compare failing test expectations with current codebase UX elements. "
        "If an element has an equivalent handler (e.g. handleCheckout / handlePayment) or evolved text, "
        "classify as LOCATOR_DRIFT (False Positive) rather than UI_FUNCTIONAL_REGRESSION."
    )
    return "\n".join(lines)


def analyze_ux_false_positive(
    ux_elements: list[dict[str, Any]],
    failed_target: dict[str, Any],
) -> dict[str, Any] | None:
    """Evaluate whether an E2E test failure is a False Positive caused by UX drift."""
    target_name = (failed_target.get("name") or failed_target.get("text") or "").strip().lower()
    if not target_name:
        return None

    import difflib

    UX_SYNONYMS = {
        "order": {"purchase", "checkout", "buy", "pay", "payment"},
        "pay": {"payment", "checkout", "order", "purchase"},
        "payment": {"pay", "checkout", "order", "purchase"},
        "purchase": {"order", "pay", "checkout", "buy"},
        "checkout": {"order", "purchase", "pay", "payment"},
        "login": {"signin", "sign-in", "auth"},
        "signin": {"login", "log-in", "auth"},
        "submit": {"send", "confirm", "apply", "save"},
    }

    best_match = None
    best_score = 0.0

    for el in ux_elements:
        score = 0.0
        text = (el.get("text") or "").lower()
        aria = (el.get("aria_label") or "").lower()
        test_id = (el.get("test_id") or "").lower()
        handler = (el.get("handler") or "").lower()

        # 1. Direct text / aria / testid similarity
        ratio_text = difflib.SequenceMatcher(None, target_name, text).ratio() if text else 0.0
        ratio_aria = difflib.SequenceMatcher(None, target_name, aria).ratio() if aria else 0.0
        ratio_tid = difflib.SequenceMatcher(None, target_name, test_id).ratio() if test_id else 0.0
        max_ratio = max(ratio_text, ratio_aria, ratio_tid)

        # Substring / keyword matches (e.g. "pay" in "pay ${total}")
        if text and (target_name in text or text in target_name):
            max_ratio = max(max_ratio, 0.80)
        if aria and (target_name in aria or aria in target_name):
            max_ratio = max(max_ratio, 0.85)
        if test_id and (target_name in test_id or test_id in target_name):
            max_ratio = max(max_ratio, 0.85)

        score = max_ratio

        # 2. Semantic token & Handler match boost
        words = re.findall(r'[a-zA-Z]+', target_name)
        synonyms: set[str] = set()
        for w in words:
            synonyms.update(UX_SYNONYMS.get(w.lower(), set()))
        all_semantic_tokens = {w.lower() for w in words} | synonyms

        if any(tok in handler or tok in text for tok in all_semantic_tokens if len(tok) >= 3):
            score = max(score, 0.88)

        # 3. Disabled guard detection
        is_disabled = bool(el.get("disabled_guard"))

        if score > best_score and score >= 0.50:
            best_score = score
            suggested = (
                f'[data-testid="{el["test_id"]}"]'
                if el.get("test_id")
                else (f'button:has-text("{el["text"]}")' if el.get("text") else f'[aria-label="{el["aria_label"]}"]')
            )
            best_match = {
                "element": el,
                "confidence": round(score, 2),
                "is_false_positive": True,
                "has_disabled_guard": is_disabled,
                "suggested_selector": suggested,
            }

    return best_match


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
