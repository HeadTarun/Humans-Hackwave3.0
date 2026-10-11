"""Deployment Smoke Test Script for HealTest Backend.

Usage:
    python scripts/smoke_test.py [--url http://localhost:8000] [--token <optional-jwt>]

Runs end-to-end API verification against a running backend instance.
"""
import argparse
import sys
import uuid
import httpx


def run_smoke_test(base_url: str, token: str | None = None) -> bool:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-User-ID"] = f"smoke-test-{uuid.uuid4().hex[:6]}"

    print(f"\n=======================================================")
    print(f" HealTest Backend Smoke Test: {base_url}")
    print(f" Caller ID: {headers.get('X-User-ID', 'Bearer Token')}")
    print(f"=======================================================\n")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        # 1. Health check
        print("[1/7] Testing GET /health...")
        try:
            r = client.get("/health")
            if r.status_code != 200 or r.json().get("status") != "ok":
                print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
                return False
            data = r.json()
            print(f"  [PASS] Health OK: version={data.get('version')} services={data.get('services')}")
        except Exception as e:
            print(f"  [FAIL] FAILED to connect: {e}")
            return False

        # 2. CORS preflight check
        print("[2/7] Testing CORS Preflight OPTIONS /projects...")
        r = client.options(
            "/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-User-ID,Content-Type",
            },
        )
        if r.status_code != 200 or not r.headers.get("access-control-allow-origin"):
            print(f"  [FAIL]: Status {r.status_code}, Headers: {dict(r.headers)}")
            return False
        print(f"  [PASS] CORS OK: Allow-Origin={r.headers.get('access-control-allow-origin')}")

        # 3. Create Project
        print("[3/7] Testing POST /projects...")
        project_name = f"Smoke Project {uuid.uuid4().hex[:4]}"
        r = client.post("/projects", json={"name": project_name}, headers=headers)
        if r.status_code != 201:
            print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
            return False
        project = r.json()
        project_id = project["id"]
        print(f"  [PASS] Created Project: id={project_id} name={project['name']}")

        # 4. List Projects
        print("[4/7] Testing GET /projects...")
        r = client.get("/projects", headers=headers)
        if r.status_code != 200:
            print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
            return False
        projects = r.json().get("projects", [])
        if not any(p["id"] == project_id for p in projects):
            print(f"  [FAIL]: Project {project_id} not found in listing")
            return False
        print(f"  [PASS] List Projects OK: found {len(projects)} project(s)")

        # 5. Create Test Definition
        print("[5/7] Testing POST /projects/{project_id}/tests...")
        test_payload = {
            "name": "Smoke Checkout Flow",
            "original_intent": "Navigate to store, click checkout button, and verify order confirmed",
            "required_assertions": [
                {
                    "type": "visible",
                    "target": {"text": "Order confirmed"},
                    "description": "Order confirmed message is visible",
                }
            ],
            "action_plan": {
                "steps": [
                    {"action": "navigate", "target": "http://example.com"},
                    {"action": "click", "target": {"role": "button", "name": "Checkout"}},
                ],
                "required_assertions": [
                    {
                        "type": "visible",
                        "target": {"text": "Order confirmed"},
                        "description": "Order confirmed message is visible",
                    }
                ],
            },
        }
        r = client.post(f"/projects/{project_id}/tests", json=test_payload, headers=headers)
        if r.status_code != 201:
            print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
            return False
        test_def = r.json()
        test_id = test_def["id"]
        print(f"  [PASS] Created Test: id={test_id} name={test_def['name']}")

        # 6. List Tests
        print(f"[6/7] Testing GET /projects/{project_id}/tests...")
        r = client.get(f"/projects/{project_id}/tests", headers=headers)
        if r.status_code != 200:
            print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
            return False
        tests = r.json().get("tests", [])
        if not any(t["id"] == test_id for t in tests):
            print(f"  [FAIL]: Test {test_id} not found in project tests")
            return False
        print(f"  [PASS] List Tests OK: found {len(tests)} test(s)")

        # 7. Start Test Run
        print(f"[7/7] Testing POST /tests/{test_id}/runs...")
        r = client.post(f"/tests/{test_id}/runs", json={}, headers=headers)
        if r.status_code != 201:
            print(f"  [FAIL]: Status {r.status_code}, Body: {r.text}")
            return False
        run = r.json()
        run_id = run["id"]
        print(f"  [PASS] Run Executed: id={run_id} status={run.get('status')} classification={run.get('classification')}")

    print("\n=======================================================")
    print(" ALL SMOKE TESTS PASSED! Backend is deployment-ready.")
    print("=======================================================\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HealTest Backend Smoke Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of backend")
    parser.add_argument("--token", default=None, help="Bearer token for auth (optional)")
    args = parser.parse_args()

    success = run_smoke_test(args.url, args.token)
    sys.exit(0 if success else 1)
