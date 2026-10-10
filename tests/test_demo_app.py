"""Tests for the demo app revisions.

Tests verify that the three demo app revisions behave as expected:
- Revision A: action plan passes ✅
- Revision B: locator drift (button renamed "Complete Order") — step fails
- Revision C: API failure (500 from /api/checkout) — network error detected

These tests use a local HTTP server (no E2B) and Playwright.
They are skipped when Playwright is not installed.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

import pytest

from app.models.domain import (
    ActionPlan,
    ActionStep,
    ActionTarget,
    ActionType,
    RequiredAssertion,
)


# ---------------------------------------------------------------------------
# Local demo-app simulation servers
# ---------------------------------------------------------------------------

def _make_handler(revision: str) -> type:
    """Factory for BaseHTTPRequestHandler with a specific revision baked in."""

    BUTTON_LABEL = {
        "a": "Checkout",
        "b": "Complete Order",
        "c": "Checkout",
    }[revision]

    BUTTON_ID = {
        "a": "checkout-btn",
        "b": "complete-order-btn",
        "c": "checkout-btn",
    }[revision]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                html = f"""<!DOCTYPE html>
<html>
<head><title>Demo Store — Rev {revision.upper()}</title></head>
<body>
  <h1>Demo Store</h1>
  <p>Revision: <strong>{revision.upper()}</strong></p>
  <button id="{BUTTON_ID}" role="button">{BUTTON_LABEL}</button>
  <div id="status"></div>
  <script>
    document.getElementById('{BUTTON_ID}').addEventListener('click', function() {{
      fetch('/api/checkout', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}})
        .then(r => r.json())
        .then(data => {{
          if (data.success) {{
            document.getElementById('status').textContent = 'Order confirmed';
          }} else {{
            document.getElementById('status').textContent = 'Checkout failed';
          }}
        }})
        .catch(function(e) {{
          document.getElementById('status').textContent = 'Network error';
        }});
    }});
  </script>
</body>
</html>""".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif self.path == "/health":
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/api/checkout":
                if revision == "c":
                    body = b'{"error":"Internal server error"}'
                    self.send_response(500)
                else:
                    body = b'{"success":true,"orderId":"ORD-DEMO-001"}'
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    Handler.__name__ = f"Handler_{revision}"
    return Handler


def _start_server(revision: str) -> tuple[str, HTTPServer]:
    """Start a local HTTP server for the given revision. Returns (base_url, server)."""
    handler = _make_handler(revision)
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", server


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

def _plan_for_revision_a(base_url: str) -> ActionPlan:
    """Action plan targeting the 'Checkout' button (revision A label)."""
    return ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target=base_url),
            ActionStep(
                action=ActionType.CLICK,
                target=ActionTarget(role="button", name="Checkout"),
            ),
            ActionStep(
                action=ActionType.WAIT,
                target=ActionTarget(text="wait"),
                value="800",
            ),
        ],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="Order confirmed"),
                description="Order confirmation message shown",
            )
        ],
    )


def _plan_for_revision_b(base_url: str) -> ActionPlan:
    """Same plan as A — 'Checkout' button; will fail on rev B (renamed to 'Complete Order')."""
    return _plan_for_revision_a(base_url)


def _plan_for_revision_c(base_url: str) -> ActionPlan:
    """Plan for rev C — same button label works but API returns 500."""
    return _plan_for_revision_a(base_url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


pw_skip = pytest.mark.skipif(
    not _playwright_available(),
    reason="Playwright not installed",
)


# ---------------------------------------------------------------------------
# Revision A — should PASS
# ---------------------------------------------------------------------------

@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_a_passes():
    """Revision A: correct button label → plan succeeds → 'Order confirmed' visible."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    base_url, server = _start_server("a")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        plan = _plan_for_revision_a(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        # All assertions should pass
        assert all(r["passed"] for r in evidence.assertion_results), (
            f"Some assertions failed: {evidence.assertion_results}"
        )
        assert evidence.passed is True
        assert evidence.steps_failed == 0
    finally:
        server.shutdown()


@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_a_no_network_failures():
    """Revision A: checkout API returns 200, no network failures."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    base_url, server = _start_server("a")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        plan = _plan_for_revision_a(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        api_failures = [f for f in evidence.network_failures if f["status"] >= 500]
        assert api_failures == [], f"Unexpected API failures: {api_failures}"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Revision B — locator drift (button renamed)
# ---------------------------------------------------------------------------

@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_b_fails_on_locator():
    """Revision B: button renamed to 'Complete Order' → old plan's locator fails."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    base_url, server = _start_server("b")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        # Plan still looks for 'Checkout' button — won't find it
        plan = _plan_for_revision_b(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        # Click step fails (button not found)
        assert evidence.steps_failed >= 1, "Expected click step to fail on locator drift"
        assert evidence.failed_step_index == 1  # click is step index 1
    finally:
        server.shutdown()


@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_b_checkout_button_exists_with_new_label():
    """Revision B: 'Complete Order' button is present and clickable with the right name."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    base_url, server = _start_server("b")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        # Updated plan using the new button label
        updated_plan = ActionPlan(
            steps=[
                ActionStep(action=ActionType.NAVIGATE, target=base_url),
                ActionStep(
                    action=ActionType.CLICK,
                    target=ActionTarget(role="button", name="Complete Order"),
                ),
                ActionStep(
                    action=ActionType.WAIT,
                    target=ActionTarget(text="wait"),
                    value="800",
                ),
            ],
            required_assertions=[
                RequiredAssertion(
                    type="visible",
                    target=ActionTarget(text="Order confirmed"),
                )
            ],
        )
        evidence = await runner.execute_plan(base_url, updated_plan)
        # With the corrected locator, it should pass
        assert evidence.steps_failed == 0
        assert all(r["passed"] for r in evidence.assertion_results)
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Revision C — API failure (500)
# ---------------------------------------------------------------------------

@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_c_api_returns_500():
    """Revision C: /api/checkout returns 500, captured as network failure."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    base_url, server = _start_server("c")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        plan = _plan_for_revision_c(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        # Network failure must be recorded
        api_failures = [f for f in evidence.network_failures if f["status"] >= 500]
        assert len(api_failures) >= 1, (
            f"Expected a 500 network failure, got: {evidence.network_failures}"
        )
        assert api_failures[0]["url"].endswith("/api/checkout")
    finally:
        server.shutdown()


@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_c_classified_as_api_failure():
    """Revision C network failure is classified as API_FAILURE by DeterministicClassifier."""
    from app.services.browser_runner import BrowserEvidence, PlaywrightBrowserRunner
    from app.services.run_orchestrator import DeterministicClassifier
    from app.models.domain import FailureCategory

    base_url, server = _start_server("c")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        plan = _plan_for_revision_c(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        classifier = DeterministicClassifier()
        result = await classifier.classify(evidence.to_dict())

        assert result.category == FailureCategory.API_FAILURE
        assert result.requires_human_review is False
    finally:
        server.shutdown()


@pw_skip
@pytest.mark.asyncio
async def test_demo_revision_c_not_auto_healed():
    """Revision C is a real API failure, not a locator issue — should not be misclassified."""
    from app.services.browser_runner import PlaywrightBrowserRunner
    from app.services.run_orchestrator import DeterministicClassifier
    from app.models.domain import FailureCategory

    base_url, server = _start_server("c")
    try:
        runner = PlaywrightBrowserRunner(headless=True)
        plan = _plan_for_revision_c(base_url)
        evidence = await runner.execute_plan(base_url, plan)

        classifier = DeterministicClassifier()
        result = await classifier.classify(evidence.to_dict())

        # Must NOT be classified as locator drift (which is auto-healable)
        assert result.category != FailureCategory.LOCATOR_DRIFT
    finally:
        server.shutdown()
