"""Tests for immutability of original_intent, required_assertions, and test versions."""
import pytest
from tests.conftest import HEADERS, MINIMAL_ACTION_PLAN, MINIMAL_TEST_BODY


class TestIntentImmutability:
    """original_intent and required_assertions must be set once and never changed."""

    def test_original_intent_preserved_after_version_creation(self, client, project_id):
        body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        test_id = r.json()["id"]
        original_intent = r.json()["original_intent"]

        # Get the test — intent must be unchanged
        r2 = client.get(f"/tests/{test_id}", headers=HEADERS)
        assert r2.json()["original_intent"] == original_intent

    def test_required_assertions_preserved_in_version_snapshot(self, client, project_id):
        """Version's assertions_snapshot must match the definition's required_assertions."""
        body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        data = r.json()

        definition_assertions = data["required_assertions"]
        snapshot_assertions = data["current_version"]["assertions_snapshot"]

        assert len(snapshot_assertions) == len(definition_assertions)
        for d, s in zip(definition_assertions, snapshot_assertions):
            assert d["type"] == s["type"]

    def test_no_endpoint_to_modify_intent(self, client, test_id):
        """There is no PATCH /tests/{id} — intent cannot be mutated via API."""
        r = client.patch(f"/tests/{test_id}", json={"original_intent": "hacked"}, headers=HEADERS)
        # 405 Method Not Allowed (route doesn't exist)
        assert r.status_code == 405

    def test_no_endpoint_to_delete_test_version(self, client, test_id_with_version):
        """Versions are append-only — no delete endpoint."""
        r2 = client.get(f"/tests/{test_id_with_version}", headers=HEADERS)
        version_id = r2.json()["active_version_id"]
        r = client.delete(f"/versions/{version_id}", headers=HEADERS)
        assert r.status_code == 404  # Route doesn't exist


class TestVersionAppendOnly:
    def test_creating_test_with_plan_makes_version_1(self, client, project_id):
        body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
        r = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r.json()["current_version"]["version_number"] == 1

    def test_second_test_also_starts_at_version_1(self, client, project_id):
        """Each test definition has its own version sequence."""
        body = {**MINIMAL_TEST_BODY, "action_plan": MINIMAL_ACTION_PLAN}
        r1 = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        r2 = client.post(f"/projects/{project_id}/tests", json=body, headers=HEADERS)
        assert r1.json()["current_version"]["version_number"] == 1
        assert r2.json()["current_version"]["version_number"] == 1


class TestRepairProposalIntegrity:
    """A repair proposal must not weaken assertions."""

    def _walk_to_repair_proposed(self, client, run_id):
        for s in ["preparing", "running", "collecting_evidence", "classifying", "repair_proposed"]:
            client.post(f"/runs/{run_id}/transition", json={"status": s}, headers=HEADERS)

    def test_repair_proposal_rejected_if_fewer_assertions(
        self, client, test_id_with_version
    ):
        run_r = client.post(
            f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS
        )
        run_id = run_r.json()["id"]
        self._walk_to_repair_proposed(client, run_id)

        # Proposal with NO assertions — should be rejected
        weakened_plan = {
            "steps": [{"action": "navigate", "target": "http://localhost"}],
            "required_assertions": [],  # Empty — weakening the assertions
        }
        r = client.post(
            f"/runs/{run_id}/repair-proposals",
            json={
                "proposed_plan": weakened_plan,
                "reason": "Tried to remove assertions",
                "confidence": 0.9,
            },
            headers=HEADERS,
        )
        # Empty assertions rejected at Pydantic validation level (422)
        assert r.status_code == 422

    def test_repair_proposal_requires_repair_proposed_status(
        self, client, run_id
    ):
        """Cannot create a proposal on a queued run — must be repair_proposed status."""
        valid_plan = {
            "steps": [{"action": "navigate", "target": "http://localhost"}],
            "required_assertions": [{"type": "visible", "target": {"text": "OK"}}],
        }
        r = client.post(
            f"/runs/{run_id}/repair-proposals",
            json={"proposed_plan": valid_plan, "reason": "fix", "confidence": 0.8},
            headers=HEADERS,
        )
        assert r.status_code == 409

    def test_approve_requires_validated_pass_status(
        self, client, test_id_with_version
    ):
        """Cannot approve a proposal that hasn't been validated yet."""
        run_r = client.post(
            f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS
        )
        run_id = run_r.json()["id"]
        self._walk_to_repair_proposed(client, run_id)

        valid_plan = {
            "steps": [{"action": "navigate", "target": "http://localhost"}],
            "required_assertions": [{"type": "visible", "target": {"text": "Order confirmed"}}],
        }
        proposal_r = client.post(
            f"/runs/{run_id}/repair-proposals",
            json={"proposed_plan": valid_plan, "reason": "fix locator", "confidence": 0.85},
            headers=HEADERS,
        )
        assert proposal_r.status_code == 201
        proposal_id = proposal_r.json()["id"]

        # Try to approve without validating — should fail
        r = client.post(
            f"/repair-proposals/{proposal_id}/approve",
            json={"change_reason": "approve"},
            headers=HEADERS,
        )
        assert r.status_code == 409
