"""Tests for domain model validation — Pydantic schemas, enums, action plan rules."""
import pytest
from pydantic import ValidationError

from app.models.domain import (
    ActionPlan,
    ActionStep,
    ActionTarget,
    ActionType,
    ArtifactType,
    FailureCategory,
    RepairValidationStatus,
    RequiredAssertion,
    RunStatus,
)


class TestActionTarget:
    def test_valid_by_role_and_name(self):
        t = ActionTarget(role="button", name="Checkout")
        assert t.role == "button"
        assert t.name == "Checkout"

    def test_valid_by_text(self):
        t = ActionTarget(text="Order confirmed")
        assert t.text == "Order confirmed"

    def test_valid_by_url(self):
        t = ActionTarget(url="http://localhost:3000")
        assert t.url == "http://localhost:3000"

    def test_valid_by_selector(self):
        t = ActionTarget(selector="#submit-btn")
        assert t.selector == "#submit-btn"

    def test_requires_at_least_one_field(self):
        with pytest.raises(ValidationError, match="at least one targeting field"):
            ActionTarget()

    def test_all_none_raises(self):
        with pytest.raises(ValidationError):
            ActionTarget(role=None, name=None, text=None, url=None, selector=None)


class TestActionStep:
    def test_valid_navigate_with_string_target(self):
        step = ActionStep(action=ActionType.NAVIGATE, target="http://localhost")
        assert step.action == ActionType.NAVIGATE

    def test_valid_click_with_target_object(self):
        step = ActionStep(
            action=ActionType.CLICK,
            target=ActionTarget(role="button", name="Buy"),
        )
        assert step.action == ActionType.CLICK

    def test_invalid_action_type_rejected(self):
        with pytest.raises(ValidationError):
            ActionStep(action="exec_shell", target="something")

    def test_timeout_bounds(self):
        with pytest.raises(ValidationError):
            ActionStep(action=ActionType.CLICK, target="x", timeout_ms=500)  # too low
        with pytest.raises(ValidationError):
            ActionStep(action=ActionType.CLICK, target="x", timeout_ms=999_999)  # too high

    def test_fill_with_value(self):
        step = ActionStep(
            action=ActionType.FILL,
            target=ActionTarget(selector="#email"),
            value="test@example.com",
        )
        assert step.value == "test@example.com"


class TestActionPlan:
    def test_valid_plan(self):
        plan = ActionPlan(
            steps=[ActionStep(action=ActionType.NAVIGATE, target="http://localhost")],
            required_assertions=[
                RequiredAssertion(type="visible", target=ActionTarget(text="Hello"))
            ],
        )
        assert len(plan.steps) == 1
        assert len(plan.required_assertions) == 1

    def test_empty_steps_rejected(self):
        with pytest.raises(ValidationError):
            ActionPlan(
                steps=[],
                required_assertions=[
                    RequiredAssertion(type="visible", target=ActionTarget(text="x"))
                ],
            )

    def test_empty_assertions_rejected(self):
        with pytest.raises(ValidationError):
            ActionPlan(
                steps=[ActionStep(action=ActionType.NAVIGATE, target="http://x")],
                required_assertions=[],
            )


class TestEnumCompleteness:
    def test_run_status_values(self):
        expected = {
            "queued", "preparing", "running", "collecting_evidence",
            "classifying", "repair_proposed", "rerunning",
            "passed", "failed", "needs_review", "environment_failed",
        }
        assert {s.value for s in RunStatus} == expected

    def test_failure_category_values(self):
        expected = {
            "LOCATOR_DRIFT", "UI_FUNCTIONAL_REGRESSION", "API_FAILURE",
            "CONTRACT_MISMATCH", "ASSERTION_FAILURE", "ENVIRONMENT_FAILURE",
            "AMBIGUOUS_REQUIRES_REVIEW",
        }
        assert {c.value for c in FailureCategory} == expected

    def test_action_type_allowlist(self):
        expected = {"navigate", "click", "fill", "select", "assert_visible", "assert_text", "assert_url", "wait"}
        assert {a.value for a in ActionType} == expected

    def test_artifact_type_values(self):
        assert ArtifactType.SCREENSHOT.value == "screenshot"
        assert ArtifactType.TRACE.value == "trace"

    def test_repair_validation_status_values(self):
        expected = {"pending", "validating", "validated_pass", "validated_fail", "approved", "rejected"}
        assert {s.value for s in RepairValidationStatus} == expected
