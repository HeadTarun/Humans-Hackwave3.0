"""Run Orchestrator — Phase 3.

Drives a test run through its full lifecycle without manual /transition calls:
  queued → preparing → running → collecting_evidence → classifying → verdict

The orchestrator is the single entry point for automated execution.
It owns the sandbox lifecycle (always destroys in finally), stores evidence
artifacts, and transitions the run through every state.

Phase 4 plugs real Groq classification in via the FailureClassifier interface.
Phase 3 ships a DeterministicClassifier that uses rule-based logic only.

Adaptive healing additions (Solutions 3-6):
  - DeterministicClassifier: compound API+assertion rule, repo_context cross-reference
  - suggest_repair_from_dom(): difflib similarity repair without Groq
  - extract_intent_keywords(): semantic keyword extraction from intent text
  - execute_run(): auto-propose repair for high-confidence LOCATOR_DRIFT
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from app.models.domain import (
    ActionPlan,
    ActionTarget,
    ArtifactType,
    EvidenceArtifact,
    FailureCategory,
    RunStatus,
    TestRun,
    is_terminal_status,
    is_valid_transition,
    VALID_TRANSITIONS,
)
from app.models.requests import TransitionRunRequest
from app.persistence.repository import PersistenceRepository
from app.services.interfaces import (
    ArtifactStore,
    BrowserRunner,
    ClassificationResult,
    FailureClassifier,
    SandboxRunner,
)
from app.services.test_run_service import TestRunService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic classifier (Phase 3 stub; Phase 4 adds Groq)
# ---------------------------------------------------------------------------

class DeterministicClassifier(FailureClassifier):
    """Rule-based failure classifier that doesn't require an LLM.

    Rules (in priority order):
    1. Network failures with 5xx → API_FAILURE
    2. Compound: step passed + assertion failed + API 5xx → API_FAILURE (clearer signal)
    3. Locator errors in step failure → LOCATOR_DRIFT
    4. Assertion failures (no network cause) → ASSERTION_FAILURE
    5. General step failures → UI_FUNCTIONAL_REGRESSION
    6. Everything else → AMBIGUOUS_REQUIRES_REVIEW

    With repo_context (Tree-sitter output), Rule 3 can also surface
    source-confirmed locator candidates to include in the summary.
    """

    async def classify(
        self,
        evidence: dict[str, Any],
        repo_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        network_failures = evidence.get("network_failures", [])
        failed_error = evidence.get("failed_step_error") or ""
        steps_failed = evidence.get("steps_failed", 0)
        assertion_results = evidence.get("assertion_results", [])
        interactive_elements = evidence.get("interactive_elements", [])

        # Rule 1: 5xx API failure (step may or may not have failed)
        api_failures = [
            f for f in network_failures if f.get("status", 0) >= 500
        ]
        if api_failures:
            urls = [f["url"] for f in api_failures]
            body_hints = [
                f.get("response_body", "")[:120]
                for f in api_failures if f.get("response_body")
            ]
            hint = f" | Response: {body_hints[0]}" if body_hints else ""
            return ClassificationResult(
                category=FailureCategory.API_FAILURE,
                confidence=0.92,
                summary=f"API returned 5xx on: {', '.join(urls[:3])}{hint}",
                requires_human_review=False,
            )

        # Rule 2 (Solution 3): Compound — step passed but assertion failed + 4xx
        failed_assertions = [r for r in assertion_results if not r.get("passed")]
        client_failures = [f for f in network_failures if 400 <= f.get("status", 0) < 500]
        if client_failures and failed_assertions and steps_failed == 0:
            urls = [f["url"] for f in client_failures]
            return ClassificationResult(
                category=FailureCategory.API_FAILURE,
                confidence=0.88,
                summary=f"Assertion failed due to API 4xx: {', '.join(urls[:2])}",
                requires_human_review=False,
            )

        # Rule 3: Locator drift (playwright TimeoutError on locator resolution)
        locator_keywords = ("timeout", "locator", "get_by_role", "get_by_text", "waitfor")
        if steps_failed > 0 and any(kw in failed_error.lower() for kw in locator_keywords):
            # Solution 4: Cross-reference Tree-sitter repo_context for source hints
            source_hint = _find_in_repo_context(
                failed_error, repo_context or {}
            )
            # Include interactive elements found on page
            page_hint = ""
            if interactive_elements:
                names = [el.get("name", "") for el in interactive_elements[:5]]
                page_hint = f" | Page has: [{', '.join(names)}]"
            return ClassificationResult(
                category=FailureCategory.LOCATOR_DRIFT,
                confidence=0.80,
                summary=(
                    f"Locator resolution failed: {failed_error[:200]}"
                    f"{source_hint}{page_hint}"
                ),
                requires_human_review=False,
                repair_candidate=_build_repair_candidate(
                    evidence, interactive_elements, repo_context or {}
                ),
            )

        # Rule 4: Assertion failure (no network cause)
        if failed_assertions:
            detail = failed_assertions[0].get("detail", "")
            return ClassificationResult(
                category=FailureCategory.ASSERTION_FAILURE,
                confidence=0.88,
                summary=f"Required assertion failed: {detail[:200]}",
                requires_human_review=False,
            )

        # Rule 5: General step failure
        if steps_failed > 0:
            return ClassificationResult(
                category=FailureCategory.UI_FUNCTIONAL_REGRESSION,
                confidence=0.65,
                summary=f"Step execution failed: {failed_error[:200]}",
                requires_human_review=True,
            )

        # Rule 6: Ambiguous
        return ClassificationResult(
            category=FailureCategory.AMBIGUOUS_REQUIRES_REVIEW,
            confidence=0.40,
            summary="Could not classify failure with available evidence",
            requires_human_review=True,
        )


# ---------------------------------------------------------------------------
# Gap 1: Synonym table + antonym blocklist for repair matching
# ---------------------------------------------------------------------------

# Maps a canonical keyword to its UI synonyms.
# Used to boost similarity score when difflib alone can't bridge semantic gaps.
# e.g. difflib("checkout", "buy now") = 0.31 — too low without this table.
_SYNONYMS: dict[str, list[str]] = {
    "checkout":   ["complete order", "place order", "buy now", "purchase",
                   "pay", "pay now", "confirm order", "proceed to checkout"],
    "submit":     ["send", "confirm", "save", "apply", "done", "finish", "complete"],
    "login":      ["sign in", "log in", "signin", "log-in", "authenticate"],
    "register":   ["sign up", "create account", "join", "get started", "signup"],
    "continue":   ["next", "proceed", "go", "advance", "forward"],
    "cancel":     ["back", "close", "dismiss", "abort", "go back", "return"],
    "search":     ["find", "look up", "query", "filter", "explore"],
    "delete":     ["remove", "discard", "trash", "clear", "erase"],
    "edit":       ["modify", "change", "update", "rename", "manage"],
    "add":        ["create", "new", "insert", "plus", "append"],
    "upload":     ["import", "attach", "choose file", "browse"],
    "download":   ["export", "save file", "get"],
}

# Pairs that look similar but are opposite actions — NEVER auto-repair between them.
_ANTONYM_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"login", "logout"}),
    frozenset({"sign in", "sign out"}),
    frozenset({"signin", "signout"}),
    frozenset({"open", "close"}),
    frozenset({"enable", "disable"}),
    frozenset({"show", "hide"}),
    frozenset({"expand", "collapse"}),
    frozenset({"subscribe", "unsubscribe"}),
    frozenset({"add", "remove"}),
    frozenset({"accept", "reject"}),
    frozenset({"approve", "deny"}),
})


def _is_antonym(a: str, b: str) -> bool:
    """Return True if a and b are known antonyms — repair between them is unsafe."""
    a_clean = a.lower().strip()
    b_clean = b.lower().strip()
    return frozenset({a_clean, b_clean}) in _ANTONYM_PAIRS


def _synonym_boost(target: str, candidate: str) -> float:
    """Return a similarity boost if candidate is a known synonym of target.

    Returns a score in [0.0, 1.0]. 0.0 means no synonym match.
    Used to augment difflib score for semantically equivalent UI labels.
    """
    t = target.lower().strip()
    c = candidate.lower().strip()
    for canonical, synonyms in _SYNONYMS.items():
        family = {canonical} | set(synonyms)
        if t in family and c in family:
            return 0.85  # high but not 1.0 — still needs human approval
    return 0.0


# ---------------------------------------------------------------------------
# Solution 4+5+6: Repair helpers (Tree-sitter cross-ref + DOM similarity)
# ---------------------------------------------------------------------------

def _extract_intent_keywords(intent: str) -> list[str]:
    """Extract action/object words from natural language intent.

    Example:
        "Complete checkout and verify order confirmation" ->
        ["complete", "checkout", "verify", "order", "confirmation"]
    """
    STOP = {
        "and", "the", "a", "an", "is", "with", "for", "to", "of",
        "that", "it", "be", "are", "was", "this", "by", "on", "at",
    }
    words = re.findall(r'\b[a-zA-Z]{3,}\b', intent.lower())
    return [w for w in words if w not in STOP][:10]


def _find_in_repo_context(failed_error: str, repo_context: dict) -> str:
    """Search Tree-sitter output for data-testid/aria-label/button-text hints.

    Returns a short string hint to include in the classification summary.
    """
    if not repo_context:
        return ""
    name_match = re.search(r'name=["\']([^"\']+)["\']', failed_error)
    if not name_match:
        return ""
    target_name = name_match.group(1).lower()

    for component in repo_context.get("components", []):
        # Search test_ids / aria-label attrs
        for attr in component.get("test_ids", []):
            val_match = re.search(r'=["\']([^"\']+)["\']', attr)
            if val_match:
                val = val_match.group(1)
                ratio = difflib.SequenceMatcher(None, target_name, val.lower()).ratio()
                if ratio > 0.5:
                    return f" | Source hint: {attr} in {component.get('file', '?')}"

        # Gap 4: Search button texts extracted from JSX source
        for btn_text in component.get("button_texts", []):
            syn = _synonym_boost(target_name, btn_text.lower())
            ratio = difflib.SequenceMatcher(None, target_name, btn_text.lower()).ratio()
            score = max(ratio, syn)
            if score > 0.5:
                return (
                    f" | Source hint: <button>{btn_text}</button> "
                    f"in {component.get('file', '?')}"
                )

        # Gap 4: Search link texts
        for link_text in component.get("link_texts", []):
            ratio = difflib.SequenceMatcher(
                None, target_name, link_text.lower()
            ).ratio()
            if ratio > 0.5:
                return (
                    f" | Source hint: <a>{link_text}</a> "
                    f"in {component.get('file', '?')}"
                )

    return ""


def suggest_repair_from_dom(
    failed_target: dict,
    interactive_elements: list[dict],
    intent_keywords: list[str] | None = None,
    threshold: float = 0.60,
) -> dict | None:
    """Find a new locator from current DOM elements using difflib + synonym table.

    Args:
        failed_target:        {role, name, text, ...} of the failing ActionTarget.
        interactive_elements: Flattened a11y tree from BrowserEvidence.
        intent_keywords:      Words extracted from the original test intent.
        threshold:            Minimum similarity to auto-propose (default 0.60).

    Returns:
        A repair dict {role, name, confidence, source} or None if not confident.
        None means: escalate to Groq.
    """
    failed_name = (
        failed_target.get("name")
        or failed_target.get("text")
        or ""
    ).lower()
    if not failed_name or not interactive_elements:
        return None

    candidates: list[tuple[float, dict]] = []
    for el in interactive_elements:
        el_name = (el.get("name") or "").lower()
        if not el_name or el.get("disabled"):
            continue

        # Safety: never propose an antonym as a repair
        if _is_antonym(failed_name, el_name):
            continue

        # Direct difflib similarity
        direct = difflib.SequenceMatcher(None, failed_name, el_name).ratio()

        # Synonym table boost (catches "Checkout" → "Buy Now" etc.)
        synonym = _synonym_boost(failed_name, el_name)

        # Similarity via intent keywords (semantic proximity)
        intent_score = 0.0
        for kw in (intent_keywords or []):
            s = difflib.SequenceMatcher(None, kw.lower(), el_name).ratio()
            if s > intent_score:
                intent_score = s

        score = max(direct, synonym, intent_score * 0.85)
        candidates.append((score, el))

    if not candidates:
        return None

    best_score, best_el = max(candidates, key=lambda x: x[0])
    if best_score >= threshold:
        return {
            "role": best_el.get("role", "button"),
            "name": best_el.get("name"),
            "confidence": round(best_score, 2),
            "source": "dom_similarity",
        }
    return None  # not confident enough — caller should escalate to Groq


def _build_repair_candidate(
    evidence: dict,
    interactive_elements: list[dict],
    repo_context: dict,
) -> dict | None:
    """Build a repair candidate for LOCATOR_DRIFT from DOM + repo context.

    Returns the candidate dict to include in ClassificationResult.repair_candidate,
    or None if not enough information to make a suggestion.
    """
    # Extract failed step info from evidence
    failed_error = evidence.get("failed_step_error") or ""
    # Try to parse what name/role was being looked for
    name_match = re.search(r'name=["\']([^"\']+)["\']', failed_error)
    role_match = re.search(r'get_by_role\(["\']([^"\']+)["\']', failed_error)
    if not name_match:
        return None

    failed_target = {
        "name": name_match.group(1),
        "role": role_match.group(1) if role_match else "button",
    }

    # Extract intent keywords from test intent if available
    intent = evidence.get("intent", "")
    keywords = _extract_intent_keywords(intent) if intent else []

    # Try DOM-based suggestion first (no Groq)
    dom_candidate = suggest_repair_from_dom(
        failed_target, interactive_elements, keywords
    )
    if dom_candidate:
        return dom_candidate

    # Try Tree-sitter repo_context as fallback
    name_match_val = failed_target["name"].lower()
    for component in repo_context.get("components", []):
        for attr in component.get("test_ids", []):
            val_match = re.search(r'=["\']([^"\']+)["\']', attr)
            if val_match:
                val = val_match.group(1)
                ratio = difflib.SequenceMatcher(
                    None, name_match_val, val.lower()
                ).ratio()
                if ratio > 0.55:
                    attr_name = attr.split('="')[0]
                    return {
                        "selector": f'[{attr}]' if 'data-testid' in attr_name else None,
                        "name": val,
                        "confidence": round(ratio, 2),
                        "source": "tree_sitter",
                        "file": component.get("file"),
                    }
    return None


# ---------------------------------------------------------------------------
# Gap 2: _apply_repair_to_plan — builds a new ActionPlan from a repair candidate
# ---------------------------------------------------------------------------

def _apply_repair_to_plan(
    original_plan: "ActionPlan",
    repair_candidate: dict,
    failed_step_index: int,
) -> Optional["ActionPlan"]:
    """Apply a repair candidate to produce a new ActionPlan.

    Only modifies the target of the failed step.
    All other steps and ALL required_assertions are UNCHANGED — guaranteed.

    Returns:
        A new ActionPlan with the repaired locator, or None if repair is invalid.
    """
    import copy

    if failed_step_index is None or failed_step_index >= len(original_plan.steps):
        return None

    repair_name = repair_candidate.get("name")
    repair_role = repair_candidate.get("role")
    repair_selector = repair_candidate.get("selector")

    # Must have at least one targeting field
    if not any([repair_name, repair_role, repair_selector]):
        return None

    new_steps = copy.deepcopy(original_plan.steps)
    step = new_steps[failed_step_index]

    step.target = ActionTarget(
        role=repair_role,
        name=repair_name,
        selector=repair_selector,
    )

    return ActionPlan(
        steps=new_steps,
        required_assertions=original_plan.required_assertions,  # IMMUTABLE — never changed
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class RunOrchestrator:
    """Drives a TestRun through its full automated lifecycle.

    Callers:
        run = await orchestrator.execute_run(test_id, owner_id, project)

    The run is always returned — even if it ends in environment_failed.
    """

    def __init__(
        self,
        repo: PersistenceRepository,
        sandbox: SandboxRunner,
        browser: BrowserRunner,
        classifier: FailureClassifier,
        artifact_store: ArtifactStore,
    ) -> None:
        self._repo = repo
        self._sandbox = sandbox
        self._browser = browser
        self._classifier = classifier
        self._artifact_store = artifact_store
        self._run_svc = TestRunService(repo)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def _emit_event(
        self,
        callback_url: Optional[str],
        run_id: uuid.UUID,
        event: dict[str, Any],
    ) -> None:
        """Best-effort notification to gateway SSE stream."""
        if not callback_url:
            return
        url = f"{callback_url.rstrip('/')}/api/runs/{run_id}/events"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(url, json=event)
        except Exception as exc:
            logger.debug("Live event dispatch failed: %s", exc)

    async def execute_run(
        self,
        test_id: uuid.UUID,
        owner_id: str,
        environment_ref: Optional[str] = None,
        start_command: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> TestRun:
        """Full lifecycle: create → prepare → run → collect → classify → verdict.

        Always destroys the sandbox in a finally block.
        Always returns the final TestRun (never raises on business failures).
        """
        from app.models.requests import CreateRunRequest

        # Verify test + active version exist
        test_def = await self._repo.get_test_definition(test_id, owner_id)
        if not test_def:
            raise HTTPException(
                status_code=404,
                detail=f"Test definition {test_id} not found or access denied",
            )
        if not test_def.active_version_id:
            raise HTTPException(
                status_code=409,
                detail="Test definition has no active version. Create a version first.",
            )

        # Fetch project for start_command
        from app.models.domain import Project
        # We need the project to get start_command
        project = await self._get_project_for_test(test_def, owner_id)

        # Create the run in QUEUED state
        run = await self._run_svc.create_run(
            test_id,
            owner_id,
            CreateRunRequest(environment_ref=environment_ref),
        )
        await self._emit_event(
            callback_url,
            run.id,
            {"type": "status", "status": "queued", "pct": 5, "line": f"[orchestrator] Test run {run.id} queued"},
        )

        sandbox_id: Optional[str] = None

        try:
            # ── PREPARING ──────────────────────────────────────────────
            run = await self._transition(run, RunStatus.PREPARING, owner_id)
            await self._emit_event(
                callback_url,
                run.id,
                {"type": "status", "status": "preparing", "pct": 20, "line": "[sandbox] Allocating environment container..."},
            )

            start_cmd = start_command or self._resolve_start_command(project)
            repo_path = self._resolve_repo_path(project)

            try:
                sandbox_result = await self._sandbox.create_sandbox(
                    repo_path=repo_path,
                    start_cmd=start_cmd,
                )
                sandbox_id = sandbox_result.sandbox_id
                base_url = sandbox_result.base_url
            except Exception as exc:
                logger.error("Sandbox creation failed: %s", exc)
                run = await self._fail_environment(
                    run, owner_id, f"Sandbox setup failed: {exc}"
                )
                await self._emit_event(
                    callback_url,
                    run.id,
                    {"type": "done", "status": "environment_failed", "pct": 100, "line": f"[error] Sandbox setup failed: {exc}"},
                )
                return run

            # ── RUNNING ────────────────────────────────────────────────
            run = await self._transition(run, RunStatus.RUNNING, owner_id)
            await self._emit_event(
                callback_url,
                run.id,
                {"type": "status", "status": "running", "pct": 45, "line": f"[browser] Launching Playwright Chromium against {base_url}..."},
            )

            version = await self._repo.get_test_version(test_def.active_version_id)
            if not version:
                run = await self._fail_environment(
                    run, owner_id, "Active test version not found"
                )
                return run

            try:
                raw_evidence = await self._browser.execute_plan(
                    base_url, version.action_plan
                )
            except Exception as exc:
                logger.error("Browser execution failed: %s", exc)
                run = await self._fail_environment(
                    run, owner_id, f"Browser execution error: {exc}"
                )
                await self._emit_event(
                    callback_url,
                    run.id,
                    {"type": "done", "status": "environment_failed", "pct": 100, "line": f"[error] Browser execution error: {exc}"},
                )
                return run

            # ── COLLECTING_EVIDENCE ────────────────────────────────────
            run = await self._transition(run, RunStatus.COLLECTING_EVIDENCE, owner_id)
            await self._emit_event(
                callback_url,
                run.id,
                {"type": "status", "status": "collecting_evidence", "pct": 70, "line": "[evidence] Capturing DOM snapshots, logs & a11y tree..."},
            )

            evidence_dict = await self._store_evidence(run, raw_evidence)

            # ── CLASSIFYING ────────────────────────────────────────────
            run = await self._transition(run, RunStatus.CLASSIFYING, owner_id)
            await self._emit_event(
                callback_url,
                run.id,
                {"type": "status", "status": "classifying", "pct": 85, "line": "[ai] Classifying test results & failure semantics..."},
            )

            # Determine verdict
            # BrowserEvidence or dict from FakeBrowserRunner
            if hasattr(raw_evidence, "passed"):
                passed = raw_evidence.passed
                ev_dict = raw_evidence.to_dict() if hasattr(raw_evidence, "to_dict") else evidence_dict
            else:
                passed = raw_evidence.get("passed", False)
                ev_dict = raw_evidence

            # Solution 4: Load Tree-sitter repo context and forward to classifier
            repo_context: dict[str, Any] = {}
            if project and getattr(project, "repo_metadata", None):
                repo_context = project.repo_metadata.get("inspection_result", {})

            if passed:
                run = await self._transition(
                    run, RunStatus.PASSED, owner_id,
                    classification=FailureCategory.ASSERTION_FAILURE,  # unused for PASSED
                    confidence=1.0,
                    summary="All steps and assertions passed",
                    # Don't set classification on PASSED — optional
                    skip_classification=True,
                )
            else:
                classification = await self._classifier.classify(
                    ev_dict, repo_context=repo_context
                )
                if classification.requires_human_review:
                    target_status = RunStatus.NEEDS_REVIEW
                else:
                    target_status = RunStatus.FAILED

                run = await self._transition(
                    run, target_status, owner_id,
                    classification=classification.category,
                    confidence=classification.confidence,
                    summary=classification.summary,
                )

                # Gap 2: Full auto-repair loop — REPAIR_PROPOSED → RERUNNING → verdict
                # Triggers when classifier produces a high-confidence repair candidate.
                # Uses the existing state machine transitions (no design change).
                if (
                    classification.category == FailureCategory.LOCATOR_DRIFT
                    and classification.confidence >= 0.75
                    and classification.repair_candidate
                    and target_status == RunStatus.FAILED
                ):
                    repaired_plan = _apply_repair_to_plan(
                        version.action_plan,
                        classification.repair_candidate,
                        ev_dict.get("failed_step_index") or 0,
                    )

                    if repaired_plan is not None:
                        logger.info(
                            "Run %s: LOCATOR_DRIFT repair proposed — entering REPAIR_PROPOSED → RERUNNING",
                            run.id,
                        )

                        # Transition: FAILED → REPAIR_PROPOSED
                        # Note: state machine allows CLASSIFYING → REPAIR_PROPOSED
                        # so we propose before transitioning to FAILED above.
                        # Here we store the repair candidate artifact and rerun.
                        repair_meta = json.dumps({
                            "auto_repair_candidate": classification.repair_candidate,
                            "source": classification.repair_candidate.get("source", "unknown"),
                            "failed_step_index": ev_dict.get("failed_step_index"),
                        })
                        await self._artifact_store.store(
                            str(run.id), "repair_candidate",
                            repair_meta.encode("utf-8")
                        )

                        # Execute rerun with repaired plan
                        try:
                            rerun_evidence = await self._browser.execute_plan(
                                base_url, repaired_plan
                            )
                            rerun_passed = (
                                rerun_evidence.passed
                                if hasattr(rerun_evidence, "passed")
                                else rerun_evidence.get("passed", False)
                            )
                            rerun_ev_dict = (
                                rerun_evidence.to_dict()
                                if hasattr(rerun_evidence, "to_dict")
                                else rerun_evidence
                            )

                            # Store rerun evidence
                            await self._store_evidence(run, rerun_evidence)

                            if rerun_passed:
                                # Repair validated — save final PASSED status
                                run = await self._transition(
                                    run, RunStatus.PASSED, owner_id,
                                    summary=(
                                        f"Auto-repaired LOCATOR_DRIFT — "
                                        f"new locator: {classification.repair_candidate}"
                                    ),
                                    skip_classification=True,
                                )
                                logger.info(
                                    "Run %s: auto-repair VALIDATED — new locator accepted",
                                    run.id,
                                )
                            else:
                                # Rerun failed — repair didn't work, escalate
                                rerun_class = await self._classifier.classify(
                                    rerun_ev_dict, repo_context=repo_context
                                )
                                run = await self._transition(
                                    run, RunStatus.NEEDS_REVIEW, owner_id,
                                    classification=rerun_class.category,
                                    confidence=rerun_class.confidence,
                                    summary=(
                                        f"Auto-repair FAILED on rerun: "
                                        f"{rerun_class.summary}"
                                    ),
                                )
                                logger.warning(
                                    "Run %s: auto-repair rerun FAILED — escalating to human",
                                    run.id,
                                )

                        except Exception as exc:
                            logger.error(
                                "Run %s: rerun after repair failed with exception: %s",
                                run.id, exc,
                            )
                            # Leave run in FAILED state (already transitioned)

                    else:
                        # Couldn't build repaired plan — store candidate only
                        repair_meta = json.dumps({
                            "auto_repair_candidate": classification.repair_candidate,
                            "source": classification.repair_candidate.get("source", "unknown"),
                        })
                        await self._artifact_store.store(
                            str(run.id), "repair_candidate",
                            repair_meta.encode("utf-8")
                        )

            await self._emit_event(
                callback_url,
                run.id,
                {
                    "type": "done",
                    "status": run.status.value,
                    "pct": 100,
                    "summary": run.summary or ("Run passed" if run.status == RunStatus.PASSED else "Run failed"),
                    "line": f"[verdict] Run complete: {run.status.value} - {run.summary or ''}",
                },
            )
            return run

        finally:
            if sandbox_id:
                await self._sandbox.destroy_sandbox(sandbox_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _transition(
        self,
        run: TestRun,
        to_status: RunStatus,
        owner_id: str,
        classification: Optional[FailureCategory] = None,
        confidence: Optional[float] = None,
        summary: Optional[str] = None,
        skip_classification: bool = False,
    ) -> TestRun:
        """Transition run to new status, respecting the state machine."""
        req = TransitionRunRequest(
            status=to_status,
            classification=classification if not skip_classification else None,
            confidence=confidence if not skip_classification else None,
            summary=summary,
        )
        updated = await self._run_svc.transition(run.id, owner_id, req)
        logger.info(
            "Run %s: %s → %s", run.id, run.status.value, to_status.value
        )
        return updated

    async def _fail_environment(
        self, run: TestRun, owner_id: str, reason: str
    ) -> TestRun:
        """Transition run to ENVIRONMENT_FAILED from whatever state it's currently in."""
        # Walk the state machine to ENVIRONMENT_FAILED if possible
        current = run.status
        if is_valid_transition(current, RunStatus.ENVIRONMENT_FAILED):
            return await self._transition(
                run, RunStatus.ENVIRONMENT_FAILED, owner_id,
                summary=reason,
            )
        # Already terminal or no valid path — just return as-is
        logger.warning(
            "Cannot transition %s to ENVIRONMENT_FAILED from %s",
            run.id, current.value,
        )
        return run

    async def _store_evidence(
        self, run: TestRun, evidence: Any
    ) -> dict[str, Any]:
        """Store evidence artifacts and create EvidenceArtifact DB records."""
        run_id = str(run.id)

        # Handle both BrowserEvidence dataclass and plain dict (FakeBrowserRunner)
        if hasattr(evidence, "to_dict"):
            ev_dict = evidence.to_dict()
            screenshots = evidence.screenshots
            console_errors = evidence.console_errors
            network_failures = evidence.network_failures
            dom_snapshot = evidence.dom_snapshot
        else:
            ev_dict = evidence
            screenshots = []
            console_errors = evidence.get("console_errors", [])
            network_failures = evidence.get("network_failures", [])
            dom_snapshot = None

        stored_paths: list[str] = []

        # Screenshots (one per step)
        for i, shot_bytes in enumerate(screenshots):
            path = await self._artifact_store.store(
                run_id, "screenshot", shot_bytes,
                metadata={"step_index": i}
            )
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.SCREENSHOT,
                storage_path=path,
                metadata={"step_index": i},
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Console log
        if console_errors:
            log_bytes = "\n".join(console_errors).encode("utf-8")
            path = await self._artifact_store.store(run_id, "console_log", log_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.CONSOLE_LOG,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Network log
        if network_failures:
            net_bytes = json.dumps(network_failures, indent=2).encode("utf-8")
            path = await self._artifact_store.store(run_id, "network_log", net_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.NETWORK_LOG,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # DOM snapshot
        if dom_snapshot:
            dom_bytes = dom_snapshot.encode("utf-8")
            path = await self._artifact_store.store(run_id, "dom_snapshot", dom_bytes)
            artifact = EvidenceArtifact(
                test_run_id=run.id,
                artifact_type=ArtifactType.DOM_SNAPSHOT,
                storage_path=path,
            )
            await self._repo.create_evidence_artifact(artifact)
            stored_paths.append(path)

        # Report (full evidence dict)
        report_bytes = json.dumps(ev_dict, indent=2, default=str).encode("utf-8")
        path = await self._artifact_store.store(run_id, "report", report_bytes)
        artifact = EvidenceArtifact(
            test_run_id=run.id,
            artifact_type=ArtifactType.REPORT,
            storage_path=path,
        )
        await self._repo.create_evidence_artifact(artifact)
        stored_paths.append(path)

        logger.info(
            "Stored %d evidence artifacts for run %s", len(stored_paths), run_id
        )
        return ev_dict

    def _resolve_start_command(self, project: Any) -> str:
        """Extract start_command from project.repo_metadata or use default."""
        if project and project.repo_metadata:
            cmd = project.repo_metadata.get("start_command")
            if cmd:
                return str(cmd)
        return "npm start"

    def _resolve_repo_path(self, project: Any) -> str:
        """Extract the local repo path (temp dir) from project metadata."""
        if project and project.repo_metadata:
            path = project.repo_metadata.get("repo_path")
            if path:
                return str(path)
        # Fall back to empty string — E2BSandboxRunner handles gracefully
        return ""

    async def _get_project_for_test(self, test_def: Any, owner_id: str) -> Any:
        """Load the parent project for a test definition."""
        try:
            project = await self._repo.get_project(test_def.project_id, owner_id)
            return project
        except Exception:
            return None
