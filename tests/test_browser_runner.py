"""Tests for the Playwright browser runner.

FakeBrowserRunner: configurable evidence generation and lifecycle.
PlaywrightBrowserRunner: tested against a local HTTP test server (no E2B needed).
"""
from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from app.models.domain import (
    ActionPlan,
    ActionStep,
    ActionTarget,
    ActionType,
    RequiredAssertion,
)
from app.services.interfaces import FakeBrowserRunner


# ---------------------------------------------------------------------------
# Minimal test HTTP server (no external dependencies)
# ---------------------------------------------------------------------------

_CHECKOUT_HTML = b"""<!DOCTYPE html>
<html>
<head><title>Test Store</title></head>
<body>
  <h1>Test Store</h1>
  <button role="button" id="checkout-btn">Checkout</button>
  <div id="status"></div>
  <script>
    document.getElementById('checkout-btn').addEventListener('click', () => {
      fetch('/api/checkout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
        .then(r => r.json())
        .then(data => {
          document.getElementById('status').textContent = 'Order confirmed';
        });
    });
  </script>
</body>
</html>"""

_ERROR_HTML = b"""<!DOCTYPE html>
<html><body><h1>Error Page</h1><p>Something went wrong.</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    revision = "a"

    def log_message(self, *args):
        pass  # suppress output

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_CHECKOUT_HTML)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/checkout":
            if self.__class__.revision == "c":
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"server error"}')
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success":true,"orderId":"ORD-001"}')
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def local_server():
    """Start a local HTTP server for Playwright tests. Returns base_url."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# Helpers — minimal valid plans
# ---------------------------------------------------------------------------

def _navigate_plan(base_url: str) -> ActionPlan:
    return ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target=base_url),
        ],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="Test Store"),
                description="Page title visible",
            ),
        ],
    )


def _checkout_plan(base_url: str) -> ActionPlan:
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
                value="1000",
            ),
        ],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="Test Store"),
                description="Page visible",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# FakeBrowserRunner tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_browser_runner_pass():
    runner = FakeBrowserRunner(should_pass=True)
    plan = ActionPlan(
        steps=[ActionStep(action=ActionType.NAVIGATE, target="http://localhost:3000")],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="foo"))
        ],
    )
    result = await runner.execute_plan("http://localhost:3000", plan)
    assert result["passed"] is True
    assert result["assertions_passed"] is True
    assert runner.calls[0]["base_url"] == "http://localhost:3000"
    assert runner.calls[0]["steps"] == 1


@pytest.mark.asyncio
async def test_fake_browser_runner_fail():
    runner = FakeBrowserRunner(should_pass=False)
    plan = ActionPlan(
        steps=[ActionStep(action=ActionType.NAVIGATE, target="http://localhost:3000")],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="foo"))
        ],
    )
    result = await runner.execute_plan("http://localhost:3000", plan)
    assert result["passed"] is False


@pytest.mark.asyncio
async def test_fake_browser_runner_extra_evidence():
    extra = {"console_errors": ["ReferenceError: x is not defined"], "network_failures": []}
    runner = FakeBrowserRunner(should_pass=True, extra_evidence=extra)
    plan = ActionPlan(
        steps=[ActionStep(action=ActionType.NAVIGATE, target="http://localhost:3000")],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="foo"))
        ],
    )
    result = await runner.execute_plan("http://localhost:3000", plan)
    assert result["console_errors"] == ["ReferenceError: x is not defined"]


@pytest.mark.asyncio
async def test_fake_browser_runner_call_tracking():
    runner = FakeBrowserRunner()
    plan = ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target="http://localhost:3000"),
            ActionStep(action=ActionType.CLICK, target=ActionTarget(role="button", name="OK")),
        ],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="x"))
        ],
    )
    await runner.execute_plan("http://a.com", plan)
    await runner.execute_plan("http://b.com", plan)
    assert len(runner.calls) == 2
    assert runner.calls[0]["base_url"] == "http://a.com"
    assert runner.calls[1]["base_url"] == "http://b.com"


# ---------------------------------------------------------------------------
# PlaywrightBrowserRunner tests (requires Playwright installed)
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


@pw_skip
@pytest.mark.asyncio
async def test_playwright_navigate_and_assert(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = _navigate_plan(local_server)
    evidence = await runner.execute_plan(local_server, plan)

    assert evidence.steps_executed >= 1
    assert evidence.page_url.startswith(local_server)
    assert evidence.timing_ms > 0


@pw_skip
@pytest.mark.asyncio
async def test_playwright_assertion_pass(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = ActionPlan(
        steps=[ActionStep(action=ActionType.NAVIGATE, target=local_server)],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="Test Store"),
                description="Page title",
            )
        ],
    )
    evidence = await runner.execute_plan(local_server, plan)
    assert any(r["passed"] for r in evidence.assertion_results)
    assert evidence.passed is True


@pw_skip
@pytest.mark.asyncio
async def test_playwright_assertion_fail_missing_text(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = ActionPlan(
        steps=[ActionStep(action=ActionType.NAVIGATE, target=local_server)],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="THIS TEXT DOES NOT EXIST ON PAGE 12345"),
                description="nonexistent element",
            )
        ],
    )
    evidence = await runner.execute_plan(local_server, plan)
    assert any(not r["passed"] for r in evidence.assertion_results)
    assert evidence.passed is False


@pw_skip
@pytest.mark.asyncio
async def test_playwright_screenshot_captured(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = _navigate_plan(local_server)
    evidence = await runner.execute_plan(local_server, plan)
    # At least one screenshot (after navigate step)
    assert len(evidence.screenshots) >= 1
    # PNG magic bytes
    assert evidence.screenshots[0][:4] == b"\x89PNG"


@pw_skip
@pytest.mark.asyncio
async def test_playwright_dom_snapshot_captured(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = _navigate_plan(local_server)
    evidence = await runner.execute_plan(local_server, plan)
    assert evidence.dom_snapshot is not None
    assert "html" in evidence.dom_snapshot.lower()


@pw_skip
@pytest.mark.asyncio
async def test_playwright_click_checkout_button(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = _checkout_plan(local_server)
    evidence = await runner.execute_plan(local_server, plan)
    # 3 steps: navigate, click, wait
    assert evidence.steps_executed == 3
    assert evidence.steps_failed == 0


@pw_skip
@pytest.mark.asyncio
async def test_playwright_locator_not_found_recorded(local_server):
    """A missing locator is recorded as a step failure but doesn't abort."""
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target=local_server),
            ActionStep(
                action=ActionType.CLICK,
                target=ActionTarget(role="button", name="ButtonThatDoesNotExist99999"),
                timeout_ms=2000,
            ),
        ],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="Test Store"))
        ],
    )
    evidence = await runner.execute_plan(local_server, plan)
    assert evidence.steps_failed >= 1
    assert evidence.failed_step_index == 1
    assert evidence.failed_step_error is not None


@pw_skip
@pytest.mark.asyncio
async def test_playwright_wait_action(local_server):
    from app.services.browser_runner import PlaywrightBrowserRunner

    runner = PlaywrightBrowserRunner(headless=True)
    plan = ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target=local_server),
            ActionStep(action=ActionType.WAIT, target=ActionTarget(text="wait"), value="500"),
        ],
        required_assertions=[
            RequiredAssertion(type="visible", target=ActionTarget(text="Test Store"))
        ],
    )
    evidence = await runner.execute_plan(local_server, plan)
    assert evidence.steps_executed == 2
    assert evidence.timing_ms >= 500


# ---------------------------------------------------------------------------
# BrowserEvidence dataclass
# ---------------------------------------------------------------------------

def test_browser_evidence_to_dict():
    from app.services.browser_runner import BrowserEvidence

    ev = BrowserEvidence(
        passed=True,
        steps_executed=3,
        steps_failed=0,
        failed_step_index=None,
        failed_step_error=None,
        assertion_results=[{"type": "visible", "passed": True, "detail": "ok"}],
        screenshots=[b"PNG1", b"PNG2"],
        console_errors=[],
        network_failures=[],
        dom_snapshot="<html/>",
        page_url="http://localhost:3000/",
        timing_ms=1234,
    )
    d = ev.to_dict()
    assert d["passed"] is True
    assert d["screenshot_count"] == 2
    assert d["steps_executed"] == 3
    assert d["timing_ms"] == 1234
    # Raw bytes NOT included in dict (too large for JSON)
    assert "screenshots" not in d
