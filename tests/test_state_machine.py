"""Tests for the run status state machine."""
import pytest

from app.models.domain import (
    RunStatus,
    VALID_TRANSITIONS,
    is_terminal_status,
    is_valid_transition,
)

# All statuses for convenience
ALL = list(RunStatus)
TERMINAL = {RunStatus.PASSED, RunStatus.FAILED, RunStatus.NEEDS_REVIEW, RunStatus.ENVIRONMENT_FAILED}
NON_TERMINAL = set(ALL) - TERMINAL


class TestValidTransitions:
    """Every explicitly allowed transition must be accepted."""

    def test_queued_to_preparing(self):
        assert is_valid_transition(RunStatus.QUEUED, RunStatus.PREPARING)

    def test_preparing_to_running(self):
        assert is_valid_transition(RunStatus.PREPARING, RunStatus.RUNNING)

    def test_preparing_to_environment_failed(self):
        assert is_valid_transition(RunStatus.PREPARING, RunStatus.ENVIRONMENT_FAILED)

    def test_running_to_collecting_evidence(self):
        assert is_valid_transition(RunStatus.RUNNING, RunStatus.COLLECTING_EVIDENCE)

    def test_running_to_environment_failed(self):
        assert is_valid_transition(RunStatus.RUNNING, RunStatus.ENVIRONMENT_FAILED)

    def test_collecting_evidence_to_classifying(self):
        assert is_valid_transition(RunStatus.COLLECTING_EVIDENCE, RunStatus.CLASSIFYING)

    def test_classifying_to_passed(self):
        assert is_valid_transition(RunStatus.CLASSIFYING, RunStatus.PASSED)

    def test_classifying_to_failed(self):
        assert is_valid_transition(RunStatus.CLASSIFYING, RunStatus.FAILED)

    def test_classifying_to_repair_proposed(self):
        assert is_valid_transition(RunStatus.CLASSIFYING, RunStatus.REPAIR_PROPOSED)

    def test_classifying_to_needs_review(self):
        assert is_valid_transition(RunStatus.CLASSIFYING, RunStatus.NEEDS_REVIEW)

    def test_repair_proposed_to_rerunning(self):
        assert is_valid_transition(RunStatus.REPAIR_PROPOSED, RunStatus.RERUNNING)

    def test_rerunning_to_passed(self):
        assert is_valid_transition(RunStatus.RERUNNING, RunStatus.PASSED)

    def test_rerunning_to_failed(self):
        assert is_valid_transition(RunStatus.RERUNNING, RunStatus.FAILED)

    def test_rerunning_to_needs_review(self):
        assert is_valid_transition(RunStatus.RERUNNING, RunStatus.NEEDS_REVIEW)


class TestInvalidTransitions:
    """No skipping steps, no going backwards, no self-loops."""

    def test_queued_to_passed_rejected(self):
        assert not is_valid_transition(RunStatus.QUEUED, RunStatus.PASSED)

    def test_queued_to_failed_rejected(self):
        assert not is_valid_transition(RunStatus.QUEUED, RunStatus.FAILED)

    def test_queued_to_running_rejected(self):
        assert not is_valid_transition(RunStatus.QUEUED, RunStatus.RUNNING)

    def test_running_to_passed_directly_rejected(self):
        assert not is_valid_transition(RunStatus.RUNNING, RunStatus.PASSED)

    def test_classifying_to_rerunning_rejected(self):
        assert not is_valid_transition(RunStatus.CLASSIFYING, RunStatus.RERUNNING)

    def test_passed_has_no_valid_next(self):
        for target in ALL:
            assert not is_valid_transition(RunStatus.PASSED, target)

    def test_failed_has_no_valid_next(self):
        for target in ALL:
            assert not is_valid_transition(RunStatus.FAILED, target)

    def test_needs_review_has_no_valid_next(self):
        for target in ALL:
            assert not is_valid_transition(RunStatus.NEEDS_REVIEW, target)

    def test_environment_failed_has_no_valid_next(self):
        for target in ALL:
            assert not is_valid_transition(RunStatus.ENVIRONMENT_FAILED, target)

    def test_no_self_loops(self):
        for status in ALL:
            assert not is_valid_transition(status, status), f"Self-loop allowed for {status}"


class TestTerminalStatuses:
    def test_passed_is_terminal(self):
        assert is_terminal_status(RunStatus.PASSED)

    def test_failed_is_terminal(self):
        assert is_terminal_status(RunStatus.FAILED)

    def test_needs_review_is_terminal(self):
        assert is_terminal_status(RunStatus.NEEDS_REVIEW)

    def test_environment_failed_is_terminal(self):
        assert is_terminal_status(RunStatus.ENVIRONMENT_FAILED)

    def test_non_terminal_statuses(self):
        for status in NON_TERMINAL:
            assert not is_terminal_status(status), f"{status} should NOT be terminal"

    def test_all_statuses_in_transition_table(self):
        """Every RunStatus must appear as a key in VALID_TRANSITIONS."""
        for status in RunStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"


class TestFullLifecyclePaths:
    """Simulate complete lifecycle paths through the state machine."""

    def _path_valid(self, *statuses: RunStatus) -> bool:
        for i in range(len(statuses) - 1):
            if not is_valid_transition(statuses[i], statuses[i + 1]):
                return False
        return True

    def test_happy_path(self):
        assert self._path_valid(
            RunStatus.QUEUED, RunStatus.PREPARING, RunStatus.RUNNING,
            RunStatus.COLLECTING_EVIDENCE, RunStatus.CLASSIFYING, RunStatus.PASSED,
        )

    def test_repair_path(self):
        assert self._path_valid(
            RunStatus.QUEUED, RunStatus.PREPARING, RunStatus.RUNNING,
            RunStatus.COLLECTING_EVIDENCE, RunStatus.CLASSIFYING,
            RunStatus.REPAIR_PROPOSED, RunStatus.RERUNNING, RunStatus.PASSED,
        )

    def test_environment_failure_from_preparing(self):
        assert self._path_valid(RunStatus.QUEUED, RunStatus.PREPARING, RunStatus.ENVIRONMENT_FAILED)

    def test_environment_failure_from_running(self):
        assert self._path_valid(
            RunStatus.QUEUED, RunStatus.PREPARING, RunStatus.RUNNING, RunStatus.ENVIRONMENT_FAILED
        )

    def test_ambiguous_path(self):
        assert self._path_valid(
            RunStatus.QUEUED, RunStatus.PREPARING, RunStatus.RUNNING,
            RunStatus.COLLECTING_EVIDENCE, RunStatus.CLASSIFYING, RunStatus.NEEDS_REVIEW,
        )
