"""Groq-powered failure classifier — Phase 5.

Only called when DeterministicClassifier returns:
  - FailureCategory.AMBIGUOUS_REQUIRES_REVIEW, OR
  - confidence < GROQ_CONFIDENCE_THRESHOLD

Sends a structured, bounded, redacted prompt to Groq and parses
the strict-JSON response back into a ClassificationResult.

Prompt includes:
  - Test intent (user's goal)
  - Failed step details
  - Interactive elements from accessibility tree  (from browser_runner)
  - Tree-sitter repo context                      (from repository_inspector)
  - Network failure details with response bodies  (from browser_runner)

Returns None if GROQ_API_KEY is not set — callers fall back gracefully.

Usage:
    from app.services.groq_classifier import GroqClassifier, make_groq_classifier
    classifier = make_groq_classifier()   # returns None if key not set
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from app.models.domain import FailureCategory
from app.services.interfaces import ClassificationResult, FailureClassifier

logger = logging.getLogger(__name__)

# Minimum confidence from DeterministicClassifier below which Groq is called
GROQ_CONFIDENCE_THRESHOLD = 0.75

# Groq model to use (fast, cheap, good at structured JSON)
GROQ_MODEL = "llama-3.1-8b-instant"

# Max chars of DOM / source context to include in prompt (token budget)
MAX_INTERACTIVE_ELEMENTS = 15
MAX_SOURCE_SNIPPET_CHARS = 800

# Strict JSON schema Groq must return
_RESPONSE_SCHEMA = """
{
  "classification": "<LOCATOR_DRIFT|API_FAILURE|ASSERTION_FAILURE|UI_FUNCTIONAL_REGRESSION|AMBIGUOUS_REQUIRES_REVIEW>",
  "confidence": <0.0 to 1.0>,
  "reason": "<one concise sentence>",
  "repair_candidate": null or {"role": "...", "name": "...", "selector": "..."},
  "requires_human_review": <true or false>
}
"""

_SYSTEM_PROMPT = """\
You are an expert test failure analyst for a self-healing end-to-end testing system.

Your job is to classify WHY a test failed and suggest a minimal repair if applicable.

CRITICAL RULES:
1. Return ONLY valid JSON matching the schema. No extra text, no markdown, no explanation outside JSON.
2. NEVER suggest removing or weakening an assertion to make a test pass.
3. LOCATOR_DRIFT = only the way to FIND an element changed (rename, moved). Business logic still works.
4. API_FAILURE = backend returned an error status. Do NOT suggest locator repairs for this.
5. ASSERTION_FAILURE = the user's expected outcome is not met. Requires human review.
6. Only set repair_candidate if classification is LOCATOR_DRIFT and you are confident of the new locator.
7. repair_candidate must use accessible name (role + name) or data-testid selector.
"""


class GroqClassifier(FailureClassifier):
    """Phase 5: Groq-powered failure classifier.

    Falls back to AMBIGUOUS_REQUIRES_REVIEW if the API call fails.
    """

    def __init__(self, api_key: str, model: str = GROQ_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    async def classify(
        self,
        evidence: dict[str, Any],
        repo_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """Build structured prompt, call Groq, parse response."""
        prompt = _build_prompt(evidence, repo_context or {})

        try:
            from groq import AsyncGroq  # type: ignore[import]
            client = AsyncGroq(api_key=self._api_key)
            response = await client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,   # low randomness for consistent JSON
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw_json = response.choices[0].message.content or "{}"
            return _parse_response(raw_json)

        except ImportError:
            logger.warning("groq package not installed. Run: pip install groq")
            return _fallback("groq package not installed")
        except Exception as exc:
            logger.error("Groq API call failed: %s", exc)
            return _fallback(f"Groq error: {exc}")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(evidence: dict[str, Any], repo_context: dict[str, Any]) -> str:
    """Build a structured, bounded prompt from evidence + repo context."""
    # Failed step details
    failed_step = {
        "index": evidence.get("failed_step_index"),
        "error": (evidence.get("failed_step_error") or "")[:300],
    }

    # Page elements (from accessibility tree — best for locator repair)
    page_elements = evidence.get("interactive_elements", [])[:MAX_INTERACTIVE_ELEMENTS]

    # Network failures with response bodies
    net_failures = []
    for f in evidence.get("network_failures", [])[:5]:
        net_failures.append({
            "url": f.get("url", ""),
            "status": f.get("status", 0),
            "method": f.get("method", ""),
            "body_preview": (f.get("response_body") or "")[:200],
        })

    # Source context from Tree-sitter (redacted, bounded)
    source_snippets = []
    for component in (repo_context.get("components", []) + repo_context.get("routes", []))[:5]:
        source_snippets.append({
            "file": component.get("file", ""),
            "test_ids": component.get("test_ids", [])[:10],
            "exports": component.get("exports", [])[:5],
        })

    # Failed assertions
    failed_assertions = [
        r for r in evidence.get("assertion_results", []) if not r.get("passed")
    ]

    prompt_data = {
        "intent": evidence.get("intent", "Not provided"),
        "failed_step": failed_step,
        "steps_executed": evidence.get("steps_executed", 0),
        "steps_failed": evidence.get("steps_failed", 0),
        "page_elements_when_failed": page_elements,
        "network_failures": net_failures,
        "failed_assertions": failed_assertions[:3],
        "source_context": source_snippets,
        "console_errors": evidence.get("console_errors", [])[:5],
    }

    return (
        f"Classify this E2E test failure and suggest a repair if applicable.\n\n"
        f"EVIDENCE:\n{json.dumps(prompt_data, indent=2, default=str)}\n\n"
        f"RESPONSE JSON SCHEMA:\n{_RESPONSE_SCHEMA}\n\n"
        f"Return ONLY the JSON object. No other text."
    )


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, FailureCategory] = {
    "locator_drift": FailureCategory.LOCATOR_DRIFT,
    "api_failure": FailureCategory.API_FAILURE,
    "assertion_failure": FailureCategory.ASSERTION_FAILURE,
    "ui_functional_regression": FailureCategory.UI_FUNCTIONAL_REGRESSION,
    "ambiguous_requires_review": FailureCategory.AMBIGUOUS_REQUIRES_REVIEW,
}


def _parse_response(raw_json: str) -> ClassificationResult:
    """Parse Groq JSON response into ClassificationResult."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("Groq returned invalid JSON: %s | raw: %s", exc, raw_json[:200])
        return _fallback("Groq returned invalid JSON")

    cat_str = (data.get("classification") or "").lower().replace(" ", "_")
    category = _CATEGORY_MAP.get(cat_str, FailureCategory.AMBIGUOUS_REQUIRES_REVIEW)
    confidence = float(data.get("confidence") or 0.5)
    confidence = max(0.0, min(1.0, confidence))  # clamp
    reason = str(data.get("reason") or "No reason provided")[:300]
    requires_human = bool(data.get("requires_human_review", False))
    repair_candidate = data.get("repair_candidate")

    # Validate repair_candidate — must not be empty or trivial
    if repair_candidate and not isinstance(repair_candidate, dict):
        repair_candidate = None
    if repair_candidate and not any(repair_candidate.values()):
        repair_candidate = None

    return ClassificationResult(
        category=category,
        confidence=confidence,
        summary=f"[Groq] {reason}",
        requires_human_review=requires_human,
        repair_candidate=repair_candidate,
        evidence_used=["accessibility_tree", "network_failures", "source_context"],
    )


def _fallback(reason: str) -> ClassificationResult:
    """Return a safe fallback when Groq fails."""
    return ClassificationResult(
        category=FailureCategory.AMBIGUOUS_REQUIRES_REVIEW,
        confidence=0.40,
        summary=f"Groq classification unavailable: {reason}",
        requires_human_review=True,
    )


# ---------------------------------------------------------------------------
# Factory: Cascading classifier (Deterministic → Groq)
# ---------------------------------------------------------------------------

class CascadingClassifier(FailureClassifier):
    """Tries DeterministicClassifier first. Falls back to Groq only when:
      - category is AMBIGUOUS_REQUIRES_REVIEW, OR
      - confidence < GROQ_CONFIDENCE_THRESHOLD

    This keeps Groq costs minimal — most failures are classified deterministically.
    """

    def __init__(
        self,
        deterministic: FailureClassifier,
        groq: FailureClassifier | None,
    ) -> None:
        self._det = deterministic
        self._groq = groq

    async def classify(
        self,
        evidence: dict[str, Any],
        repo_context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        result = await self._det.classify(evidence, repo_context=repo_context)

        needs_groq = (
            self._groq is not None
            and (
                result.category == FailureCategory.AMBIGUOUS_REQUIRES_REVIEW
                or result.confidence < GROQ_CONFIDENCE_THRESHOLD
            )
        )

        if needs_groq:
            logger.info(
                "Deterministic confidence %.2f < %.2f or AMBIGUOUS — escalating to Groq",
                result.confidence, GROQ_CONFIDENCE_THRESHOLD,
            )
            groq_result = await self._groq.classify(evidence, repo_context=repo_context)  # type: ignore[union-attr]
            # Use Groq result only if it's more confident
            if groq_result.confidence > result.confidence:
                return groq_result

        return result


def make_groq_classifier() -> FailureClassifier | None:
    """Build a GroqClassifier from env var, or return None if key not set.

    Usage in dependency injection:
        groq = make_groq_classifier()
        classifier = CascadingClassifier(DeterministicClassifier(), groq)
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        logger.info("GROQ_API_KEY not set — Groq classification disabled")
        return None
    return GroqClassifier(api_key=api_key)
