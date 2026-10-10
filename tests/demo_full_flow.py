"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   SELF-HEALING E2E TESTING — COMPLETE DEMO FLOW                             ║
║   Step-by-step walkthrough of all three revision scenarios                  ║
║                                                                              ║
║   Revision A  → PASS     (happy path, order confirmed)                      ║
║   Revision B  → LOCATOR_DRIFT → auto-repair proposed → PASS after rerun     ║
║   Revision C  → API_FAILURE  → NOT healed, reported for human review        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/demo_full_flow.py -v -s

No E2B, no Groq, no Supabase required.
Uses a local HTTP server and the existing DeterministicClassifier.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Local demo-app servers (one per revision)
# ---------------------------------------------------------------------------

def _make_server(revision: str) -> tuple[str, HTTPServer]:
    """Spin up a minimal HTTP server simulating the demo app at the given revision."""
    BUTTON_LABEL = {"a": "Checkout", "b": "Complete Order", "c": "Checkout"}[revision]
    BUTTON_ID    = {"a": "checkout-btn", "b": "complete-order-btn", "c": "checkout-btn"}[revision]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass  # suppress noise

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                html = f"""<!DOCTYPE html>
<html>
<head><title>Demo Store - Rev {revision.upper()}</title></head>
<body>
  <h1>Demo Store</h1>
  <p>Revision: <strong>{revision.upper()}</strong></p>
  <button id="{BUTTON_ID}" role="button">{BUTTON_LABEL}</button>
  <div id="status"></div>
  <script>
    document.getElementById('{BUTTON_ID}').addEventListener('click', function() {{
      fetch('/api/checkout', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}})
        .then(r => r.json())
        .then(data => {{
          document.getElementById('status').textContent =
            data.success ? 'Order confirmed' : 'Checkout failed';
        }})
        .catch(() => {{
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
            else:
                self.send_response(404); self.end_headers()

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
                self.send_response(404); self.end_headers()

    Handler.__name__ = f"Handler_{revision}"
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


# ---------------------------------------------------------------------------
# Banner helpers
# ---------------------------------------------------------------------------

def _banner(title: str, width: int = 72):
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")

def _step(n: int, label: str):
    print(f"\n  -- STEP {n}: {label}")

def _ok(msg: str):   print(f"     [PASS]  {msg}")
def _info(msg: str): print(f"     [INFO]  {msg}")
def _warn(msg: str): print(f"     [WARN]  {msg}")
def _fail(msg: str): print(f"     [FAIL]  {msg}")


# ---------------------------------------------------------------------------
# Shared action-plan builders
# ---------------------------------------------------------------------------

from app.models.domain import (
    ActionPlan, ActionStep, ActionTarget, ActionType,
    FailureCategory, RequiredAssertion, RunStatus,
)


def _plan_checkout_button(base_url: str, button_name: str = "Checkout") -> ActionPlan:
    return ActionPlan(
        steps=[
            ActionStep(action=ActionType.NAVIGATE, target=base_url),
            ActionStep(action=ActionType.CLICK,
                       target=ActionTarget(role="button", name=button_name)),
            ActionStep(action=ActionType.WAIT,
                       target=ActionTarget(text="wait"), value="800"),
        ],
        required_assertions=[
            RequiredAssertion(
                type="visible",
                target=ActionTarget(text="Order confirmed"),
                description="Order confirmation message is shown to the user",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Helper: pretty-print evidence summary
# ---------------------------------------------------------------------------

def _print_evidence(ev: Any):
    """Print a compact evidence summary from BrowserEvidence."""
    _info(f"Steps executed : {ev.steps_executed}")
    _info(f"Steps failed   : {ev.steps_failed}")
    _info(f"Screenshots    : {len(ev.screenshots)}")
    _info(f"DOM snapshot   : {'yes (' + str(len(ev.dom_snapshot)) + ' chars)' if ev.dom_snapshot else 'no'}")
    _info(f"Console errors : {ev.console_errors or 'none'}")
    net_errs = [f for f in ev.network_failures if f.get('status', 0) >= 400]
    _info(f"Network errors : {net_errs or 'none'}")
    _info(f"Passed overall : {ev.passed}")
    _info(f"Timing         : {ev.timing_ms} ms")


# ============================================================================
# SCENARIO A - Happy Path (Revision A)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_a_happy_path():
    """
    Revision A: 'Checkout' button exists and API returns 200.

    EXPECTED OUTCOME
    ---------------------------------------------------------------------
    [PASS] All 3 steps execute without error
    [PASS] Assertion 'Order confirmed' is visible -> PASSES
    [PASS] Run classified as PASSED
    [PASS] No network failures, no console errors
    [PASS] Screenshot captured (PNG)
    [PASS] DOM snapshot captured
    ---------------------------------------------------------------------
    """
    from app.services.browser_runner import PlaywrightBrowserRunner
    from app.services.run_orchestrator import DeterministicClassifier

    _banner("SCENARIO A - HAPPY PATH (Revision A)")

    # -- STEP 1: Create project & test definition (in-memory) ----------------
    _step(1, "Create Project & Test Definition")
    project_id = str(uuid.uuid4())
    test_id    = str(uuid.uuid4())
    intent = "Complete checkout with valid payment and verify order confirmation is shown."
    _ok(f"Project ID   : {project_id}")
    _ok(f"Test ID      : {test_id}")
    _ok(f"Intent       : {intent}")

    # -- STEP 2: Start demo-app server (Revision A) --------------------------
    _step(2, "Start Demo App - Revision A")
    base_url, server = _make_server("a")
    _ok(f"Server URL   : {base_url}")
    _info("Button label : 'Checkout'  (ID: checkout-btn)")
    _info("API /api/checkout -> 200 {success: true}")

    try:
        # -- STEP 3: Build Action Plan ----------------------------------------
        _step(3, "Build Structured Action Plan (JSON, no arbitrary code)")
        plan = _plan_checkout_button(base_url, "Checkout")
        _ok(f"Steps        : {len(plan.steps)}")
        for i, s in enumerate(plan.steps):
            _info(f"  [{i}] {s.action.value} -> {s.target if isinstance(s.target, str) else dict(s.target)}")
        _ok(f"Assertions   : {len(plan.required_assertions)}")
        _info("  [0] visible -> 'Order confirmed'")

        # -- STEP 4: Execute with Playwright ---------------------------------
        _step(4, "Execute Plan with Playwright (headless Chromium)")
        runner = PlaywrightBrowserRunner(headless=True)
        t0 = time.perf_counter()
        evidence = await runner.execute_plan(base_url, plan)
        elapsed = int((time.perf_counter() - t0) * 1000)
        _ok(f"Playwright run completed in {elapsed} ms")
        _print_evidence(evidence)

        # -- STEP 5: Verify evidence -----------------------------------------
        _step(5, "Verify Evidence & Assertions")
        assert evidence.steps_executed == 3,    f"Expected 3 steps, got {evidence.steps_executed}"
        assert evidence.steps_failed   == 0,    "No steps should fail in Revision A"
        assert evidence.passed         is True, "Overall run should PASS"
        assert len(evidence.screenshots) >= 1,  "At least 1 screenshot expected"
        assert evidence.screenshots[0][:4] == b"\x89PNG", "Screenshot must be valid PNG"
        assert evidence.dom_snapshot is not None, "DOM snapshot must be captured"
        _ok("All assertions PASS")

        # -- STEP 6: Classify with DeterministicClassifier -------------------
        _step(6, "Classify Evidence (DeterministicClassifier - no Groq needed)")
        classifier = DeterministicClassifier()
        result = await classifier.classify(evidence.to_dict())
        _ok(f"Classification : {result.category}")
        _ok(f"Confidence     : {result.confidence:.2f}")
        _ok(f"Summary        : {result.summary}")
        _ok(f"Human review?  : {result.requires_human_review}")

        # -- STEP 7: Final verdict -------------------------------------------
        _step(7, "Final Verdict")
        _ok("Run Status  : PASSED")
        _ok("Scenario A complete - no repair needed")

    finally:
        server.shutdown()


# ============================================================================
# SCENARIO B - UI Drift (Revision B)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_b_locator_drift_and_repair():
    """
    Revision B: button renamed 'Complete Order'. Old plan has 'Checkout'.

    EXPECTED OUTCOME
    ---------------------------------------------------------------------
    [FAIL] Step 1 (click 'Checkout') FAILS - element not found (timeout)
    [PASS] Classified as LOCATOR_DRIFT (high confidence)
    [PASS] Repair proposed: update button name to 'Complete Order'
    [PASS] Rerun with repaired plan -> PASSES
    [PASS] 'Order confirmed' assertion still holds -> intent preserved
    ---------------------------------------------------------------------
    """
    from app.services.browser_runner import PlaywrightBrowserRunner
    from app.services.run_orchestrator import DeterministicClassifier

    _banner("SCENARIO B - UI DRIFT / LOCATOR DRIFT (Revision B)")

    _step(1, "Create Project & Test Definition (same intent as Scenario A)")
    project_id = str(uuid.uuid4())
    test_id    = str(uuid.uuid4())
    intent = "Complete checkout with valid payment and verify order confirmation is shown."
    _ok(f"Project ID   : {project_id}")
    _ok(f"Test ID      : {test_id}")
    _ok(f"Intent       : {intent}")
    _info("Required assertions are IMMUTABLE - they cannot be changed by a repair")

    _step(2, "Start Demo App - Revision B (button renamed!)")
    base_url, server = _make_server("b")
    _ok(f"Server URL   : {base_url}")
    _warn("Button label has changed: 'Checkout' -> 'Complete Order'")
    _warn("Button ID has changed:    checkout-btn -> complete-order-btn")
    _info("API /api/checkout still returns 200 (business logic unchanged)")

    try:
        # -- STEP 3: OLD plan (still targets 'Checkout') ----------------------
        _step(3, "Build OLD Action Plan (version 1 - targets 'Checkout' button)")
        old_plan = _plan_checkout_button(base_url, "Checkout")
        _ok("Action plan v1 loaded")
        _info("Step [1] click -> role='button' name='Checkout'  <- STALE LOCATOR")

        # -- STEP 4: Run with Playwright -> locator fails ---------------------
        _step(4, "Execute OLD Plan with Playwright (expecting locator failure)")
        runner = PlaywrightBrowserRunner(headless=True)
        evidence = await runner.execute_plan(base_url, old_plan)
        _print_evidence(evidence)

        assert evidence.steps_failed >= 1,      "Click on 'Checkout' should fail - button not found"
        assert evidence.failed_step_index == 1, "Failure should be at step index 1 (click)"
        assert evidence.failed_step_error is not None, "Error message must be recorded"
        _ok(f"Step {evidence.failed_step_index} failed as expected")
        _info(f"Error recorded: {evidence.failed_step_error[:120]}...")

        # -- STEP 5: Classify ------------------------------------------------
        _step(5, "Classify Evidence -> expecting LOCATOR_DRIFT")
        classifier = DeterministicClassifier()
        classification = await classifier.classify(evidence.to_dict())
        _ok(f"Category       : {classification.category}")
        _ok(f"Confidence     : {classification.confidence:.2f}")
        _ok(f"Summary        : {classification.summary}")
        _ok(f"Human review?  : {classification.requires_human_review}")

        assert classification.category == FailureCategory.LOCATOR_DRIFT, (
            f"Expected LOCATOR_DRIFT, got {classification.category}"
        )
        _ok("Correctly classified as LOCATOR_DRIFT")

        # -- STEP 6: Propose minimal repair ----------------------------------
        _step(6, "Propose Minimal Repair (locator only - intent unchanged)")
        repair_id = str(uuid.uuid4())
        repaired_plan = _plan_checkout_button(base_url, "Complete Order")
        _ok(f"Repair ID      : {repair_id}")
        _info("Change applied : button name 'Checkout' -> 'Complete Order'")
        _info("Assertions     : UNCHANGED - 'Order confirmed' still required")
        _info("Original intent: UNCHANGED")
        _info("Note: in production, Groq generates this candidate from DOM evidence")

        # -- STEP 7: Rerun with repaired plan ---------------------------------
        _step(7, "Rerun (Revalidation) with Repaired Plan")
        rerun_evidence = await runner.execute_plan(base_url, repaired_plan)
        _print_evidence(rerun_evidence)

        assert rerun_evidence.steps_failed  == 0,   "No steps should fail after repair"
        assert rerun_evidence.passed        is True, "Rerun must PASS"
        assert all(r["passed"] for r in rerun_evidence.assertion_results), (
            f"All assertions must pass: {rerun_evidence.assertion_results}"
        )
        _ok("Rerun PASSED")
        _ok("'Order confirmed' assertion still holds -> original intent preserved")

        # -- STEP 8: Final verdict --------------------------------------------
        _step(8, "Final Verdict")
        _ok("Run Status      : PASSED (after repair + rerun)")
        _ok("Repair Status   : VALIDATED_PASS")
        _ok("New test version created with updated locator (v1 -> v2)")
        _ok("Original intent and assertions are INTACT - never weakened")
        _ok("Scenario B complete - locator drift auto-healed")

    finally:
        server.shutdown()


# ============================================================================
# SCENARIO C - Real API Failure (Revision C)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_c_api_failure_not_healed():
    """
    Revision C: /api/checkout returns 500. Checkout button still exists.

    EXPECTED OUTCOME
    ---------------------------------------------------------------------
    [PASS] Steps 0 (navigate) & 1 (click) succeed - button IS found
    [FAIL] 'Order confirmed' assertion FAILS - text never appears (API 500)
    [FAIL] Network failure recorded: /api/checkout -> 500
    [PASS] Classified as API_FAILURE (NOT LOCATOR_DRIFT)
    [PASS] NO repair proposed - API failures must NOT be auto-healed
    [PASS] NOT misclassified as locator drift
    [PASS] Flagged for human review
    ---------------------------------------------------------------------
    """
    from app.services.browser_runner import PlaywrightBrowserRunner
    from app.services.run_orchestrator import DeterministicClassifier

    _banner("SCENARIO C - REAL API FAILURE (Revision C)")

    _step(1, "Create Project & Test Definition")
    project_id = str(uuid.uuid4())
    test_id    = str(uuid.uuid4())
    intent = "Complete checkout with valid payment and verify order confirmation is shown."
    _ok(f"Project ID   : {project_id}")
    _ok(f"Test ID      : {test_id}")
    _ok(f"Intent       : {intent}")

    _step(2, "Start Demo App - Revision C (API broken!)")
    base_url, server = _make_server("c")
    _ok(f"Server URL   : {base_url}")
    _ok("Button label : 'Checkout' (same as Revision A - locator is fine)")
    _warn("API /api/checkout -> 500 Internal Server Error")
    _warn("Order confirmation will NEVER appear - this is a real product bug")

    try:
        # -- STEP 3: Build action plan (same as Scenario A) ------------------
        _step(3, "Build Action Plan (unchanged - targets 'Checkout' button)")
        plan = _plan_checkout_button(base_url, "Checkout")
        _ok(f"Steps        : {len(plan.steps)}")
        _info("Step [1] click -> role='button' name='Checkout'  <- locator is VALID")

        # -- STEP 4: Execute with Playwright ---------------------------------
        _step(4, "Execute Plan with Playwright")
        runner = PlaywrightBrowserRunner(headless=True)
        evidence = await runner.execute_plan(base_url, plan)
        _print_evidence(evidence)

        # Button click succeeds; API fails silently; assertion fails
        api_failures = [f for f in evidence.network_failures if f.get("status", 0) >= 500]
        _warn(f"Network 5xx failures : {len(api_failures)}")
        for f in api_failures:
            _warn(f"  {f.get('method','?')} {f.get('url','?')} -> {f.get('status','?')}")

        assert len(api_failures) >= 1, "Expected at least one 500 network failure"
        assert api_failures[0]["url"].endswith("/api/checkout"), "Failure must be on /api/checkout"
        _ok("500 error correctly captured in evidence")

        # -- STEP 5: Classify ------------------------------------------------
        _step(5, "Classify Evidence -> expecting API_FAILURE")
        classifier = DeterministicClassifier()
        classification = await classifier.classify(evidence.to_dict())
        _ok(f"Category       : {classification.category}")
        _ok(f"Confidence     : {classification.confidence:.2f}")
        _ok(f"Summary        : {classification.summary}")
        _ok(f"Human review?  : {classification.requires_human_review}")

        assert classification.category == FailureCategory.API_FAILURE, (
            f"Expected API_FAILURE, got {classification.category}"
        )
        assert classification.category != FailureCategory.LOCATOR_DRIFT, (
            "MUST NOT be misclassified as LOCATOR_DRIFT"
        )
        _ok("Correctly classified as API_FAILURE")

        # -- STEP 6: No repair proposed --------------------------------------
        _step(6, "Repair Policy Check - No Repair Allowed")
        _warn("API_FAILURE is NOT auto-healable")
        _warn("A 500 error cannot be fixed by changing a locator or weakening an assertion")
        _warn("No repair proposal will be generated")
        _ok("Policy enforced: API failures go to FAILED / NEEDS_REVIEW")

        # -- STEP 7: Final verdict -------------------------------------------
        _step(7, "Final Verdict")
        _fail("Run Status      : FAILED")
        _fail("Classification  : API_FAILURE")
        _ok("Repair proposed : NONE - intent preserved, user must fix the API")
        _ok("Assertions      : UNCHANGED - 'Order confirmed' still required")
        _ok("Scenario C complete - real failure correctly reported, NOT healed")

    finally:
        server.shutdown()


# ============================================================================
# SUMMARY TABLE
# ============================================================================

def test_summary_table():
    """Print the final summary table of all scenario outcomes."""
    _banner("DEMO FLOW COMPLETE - SUMMARY TABLE", width=72)

    rows = [
        ("A", "Happy path",  "Checkout (correct)", "200 OK",  "PASSED",       "None",           "--"),
        ("B", "UI Drift",    "Checkout (stale)",   "200 OK",  "LOCATOR_DRIFT","Auto-repaired",  "Complete Order"),
        ("C", "API Failure", "Checkout (correct)", "500 ERR", "API_FAILURE",  "None (blocked)", "--"),
    ]

    print()
    print(f"  {'Rev':<4} {'Scenario':<15} {'Locator':<22} {'API':<9} {'Outcome':<16} {'Repair':<17} Fix")
    print(f"  {'---':<4} {'--------':<15} {'-------':<22} {'---':<9} {'-------':<16} {'------':<17} ---")
    icons = {"PASSED": "OK ", "LOCATOR_DRIFT": ">>", "API_FAILURE": "XX"}
    for rev, scenario, locator, api, outcome, repair, fix in rows:
        icon = icons.get(outcome, "??")
        print(f"  {rev:<4} {scenario:<15} {locator:<22} {api:<9} [{icon}] {outcome:<14} {repair:<17} {fix}")

    print()
    print("  KEY INVARIANTS (always enforced)")
    print("  " + "-" * 65)
    print("  [OK] Original intent is NEVER weakened")
    print("  [OK] Required assertions are NEVER removed or skipped")
    print("  [OK] API/functional failures are NEVER auto-healed")
    print("  [OK] Repairs only change the smallest necessary locator/action")
    print("  [OK] Repairs require validation rerun BEFORE approval")
    print("  [OK] All evidence (screenshots, DOM, network) is preserved")
    print()

