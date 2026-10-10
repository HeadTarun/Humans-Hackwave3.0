"""API tests for the run lifecycle."""
import pytest
from tests.conftest import HEADERS, OTHER_HEADERS, MINIMAL_ACTION_PLAN


class TestCreateRun:
    def test_create_run_returns_201(self, client, test_id_with_version):
        r = client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
        assert r.status_code == 201

    def test_run_starts_as_queued(self, client, test_id_with_version):
        r = client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
        assert r.json()["status"] == "queued"

    def test_run_pinned_to_active_version(self, client, test_id_with_version):
        test_r = client.get(f"/tests/{test_id_with_version}", headers=HEADERS)
        active_vid = test_r.json()["active_version_id"]
        run_r = client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
        assert run_r.json()["test_version_id"] == active_vid

    def test_run_without_version_returns_409(self, client, test_id):
        """test_id fixture has no action plan → no active version."""
        r = client.post(f"/tests/{test_id}/runs", json={}, headers=HEADERS)
        assert r.status_code == 409

    def test_run_on_nonexistent_test_returns_404(self, client):
        r = client.post(
            "/tests/00000000-0000-0000-0000-000000000000/runs",
            json={},
            headers=HEADERS,
        )
        assert r.status_code == 404

    def test_other_owner_cannot_create_run(self, client, test_id_with_version):
        r = client.post(
            f"/tests/{test_id_with_version}/runs", json={}, headers=OTHER_HEADERS
        )
        assert r.status_code == 404


class TestGetRun:
    def test_get_run_returns_200(self, client, run_id):
        r = client.get(f"/runs/{run_id}", headers=HEADERS)
        assert r.status_code == 200

    def test_get_nonexistent_run_returns_404(self, client):
        r = client.get("/runs/00000000-0000-0000-0000-000000000000", headers=HEADERS)
        assert r.status_code == 404

    def test_other_owner_cannot_get_run(self, client, run_id):
        r = client.get(f"/runs/{run_id}", headers=OTHER_HEADERS)
        assert r.status_code == 404


class TestRunTransitions:
    def _transition(self, client, run_id, status, extra=None):
        body = {"status": status, **(extra or {})}
        return client.post(f"/runs/{run_id}/transition", json=body, headers=HEADERS)

    def test_queued_to_preparing(self, client, run_id):
        r = self._transition(client, run_id, "preparing")
        assert r.status_code == 200
        assert r.json()["status"] == "preparing"

    def test_started_at_set_on_first_transition(self, client, run_id):
        r = self._transition(client, run_id, "preparing")
        assert r.json()["started_at"] is not None

    def test_invalid_transition_returns_409(self, client, run_id):
        # queued → passed is not a valid transition
        r = self._transition(client, run_id, "passed")
        assert r.status_code == 409

    def test_skip_step_returns_409(self, client, run_id):
        # queued → running skips preparing
        r = self._transition(client, run_id, "running")
        assert r.status_code == 409

    def test_full_happy_path(self, client, run_id):
        transitions = [
            ("preparing", None),
            ("running", None),
            ("collecting_evidence", None),
            ("classifying", None),
            ("passed", None),
        ]
        for status, extra in transitions:
            r = self._transition(client, run_id, status, extra)
            assert r.status_code == 200, f"Failed at {status}: {r.json()}"
            assert r.json()["status"] == status

    def test_failed_requires_classification(self, client, run_id):
        # Walk to classifying first
        for s in ["preparing", "running", "collecting_evidence", "classifying"]:
            self._transition(client, run_id, s)
        # Failed without classification should be rejected
        r = self._transition(client, run_id, "failed")
        assert r.status_code == 422

    def test_failed_with_classification_succeeds(self, client, run_id):
        for s in ["preparing", "running", "collecting_evidence", "classifying"]:
            self._transition(client, run_id, s)
        r = self._transition(
            client, run_id, "failed",
            {"classification": "LOCATOR_DRIFT", "confidence": 0.9, "summary": "Button moved"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "failed"
        assert data["classification"] == "LOCATOR_DRIFT"
        assert data["confidence"] == 0.9
        assert data["finished_at"] is not None

    def test_terminal_state_cannot_transition(self, client, run_id):
        # Walk to passed
        for s in ["preparing", "running", "collecting_evidence", "classifying", "passed"]:
            self._transition(client, run_id, s)
        # Try to transition again — should be rejected
        r = self._transition(client, run_id, "preparing")
        assert r.status_code == 409

    def test_repair_lifecycle(self, client, test_id_with_version):
        """Test the repair proposal → rerunning → passed path."""
        run_r = client.post(
            f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS
        )
        run_id = run_r.json()["id"]

        for s in ["preparing", "running", "collecting_evidence", "classifying"]:
            client.post(f"/runs/{run_id}/transition", json={"status": s}, headers=HEADERS)

        r = client.post(
            f"/runs/{run_id}/transition",
            json={"status": "repair_proposed"},
            headers=HEADERS,
        )
        assert r.status_code == 200

        r = client.post(
            f"/runs/{run_id}/transition",
            json={"status": "rerunning"},
            headers=HEADERS,
        )
        assert r.status_code == 200

        r = client.post(
            f"/runs/{run_id}/transition",
            json={"status": "passed"},
            headers=HEADERS,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "passed"


class TestRunHistory:
    def test_history_returns_ordered_runs(self, client, test_id_with_version):
        # Create 3 runs
        ids = []
        for _ in range(3):
            r = client.post(
                f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS
            )
            ids.append(r.json()["id"])

        r = client.get(f"/tests/{test_id_with_version}/runs", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        returned_ids = [run["id"] for run in data["runs"]]
        assert returned_ids == ids  # Ordered by created_at

    def test_other_owner_gets_empty_history(self, client, test_id_with_version):
        client.post(f"/tests/{test_id_with_version}/runs", json={}, headers=HEADERS)
        r = client.get(f"/tests/{test_id_with_version}/runs", headers=OTHER_HEADERS)
        assert r.status_code == 404  # Other owner can't see the test either

    def test_history_for_nonexistent_test_returns_404(self, client):
        r = client.get(
            "/tests/00000000-0000-0000-0000-000000000000/runs", headers=HEADERS
        )
        assert r.status_code == 404
