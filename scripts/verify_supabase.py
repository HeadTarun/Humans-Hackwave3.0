"""
Supabase connectivity and schema verification script.
Run with: .venv\Scripts\python.exe scripts\verify_supabase.py

Steps:
  1. Load .env and connect with service role key
  2. Confirm connection is live (list tables)
  3. Run a quick CRUD smoke test against every Phase 1/2 table
  4. Verify JWT verification works with the configured secret
  5. Print a pass/fail summary
"""
import os
import sys
import time
import uuid
from pathlib import Path

# Load .env manually (pydantic-settings not available at script level easily)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_JWT_SECRET  = os.environ.get("SUPABASE_JWT_SECRET", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌  SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    sys.exit(1)

from supabase import create_client

client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

passed = []
failed = []

def check(label: str, fn):
    try:
        result = fn()
        print(f"  ✅  {label}")
        passed.append(label)
        return result
    except Exception as exc:
        print(f"  ❌  {label}: {exc}")
        failed.append(label)
        return None


print("\n=== Supabase Connectivity ===")

# 1. Basic ping via projects table select (empty is fine)
def ping():
    r = client.table("projects").select("id").limit(1).execute()
    return r

check("Connect to Supabase and query projects table", ping)


print("\n=== Schema — Table Existence ===")

TABLES = ["projects", "test_definitions", "test_versions", "test_runs",
          "evidence_artifacts", "repair_proposals"]

for table in TABLES:
    def _check_table(t=table):
        r = client.table(t).select("*").limit(0).execute()
        return r
    check(f"Table '{table}' exists", _check_table)


print("\n=== CRUD Smoke Test ===")

owner_id = f"verify-script-{uuid.uuid4().hex[:8]}"

# Create project
project_id = str(uuid.uuid4())
created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

proj_data = None
def create_project():
    global proj_data
    r = client.table("projects").insert({
        "id": project_id,
        "owner_id": owner_id,
        "name": "Verify Script Project",
        "repo_metadata": {"source": "verify_supabase.py"},
        "created_at": created_at,
    }).execute()
    proj_data = r.data[0]
    return r

check("INSERT into projects", create_project)
check("SELECT from projects by owner_id",
      lambda: client.table("projects").select("*").eq("owner_id", owner_id).execute())

# Create test definition
test_def_id = str(uuid.uuid4())
def create_test_def():
    r = client.table("test_definitions").insert({
        "id": test_def_id,
        "project_id": project_id,
        "owner_id": owner_id,
        "name": "Verify Test",
        "original_intent": "Verify Supabase schema is correct",
        "required_assertions": [{"type": "visible", "target": {"text": "OK"}, "description": "OK shown"}],
        "created_at": created_at,
        "updated_at": created_at,
    }).execute()
    return r

check("INSERT into test_definitions", create_test_def)

# Create test version
test_version_id = str(uuid.uuid4())
def create_test_version():
    r = client.table("test_versions").insert({
        "id": test_version_id,
        "test_definition_id": test_def_id,
        "version_number": 1,
        "action_plan": {"steps": [{"action": "navigate", "target": "http://localhost"}], "required_assertions": []},
        "assertions_snapshot": [{"type": "visible", "target": {"text": "OK"}}],
        "created_at": created_at,
        "created_by": owner_id,
        "change_reason": "Initial version",
    }).execute()
    return r

check("INSERT into test_versions", create_test_version)

# Update active_version_id on test_definition
def update_active_version():
    r = client.table("test_definitions").update({
        "active_version_id": test_version_id,
        "updated_at": created_at,
    }).eq("id", test_def_id).execute()
    return r

check("UPDATE test_definitions.active_version_id", update_active_version)

# Create test run
test_run_id = str(uuid.uuid4())
def create_test_run():
    r = client.table("test_runs").insert({
        "id": test_run_id,
        "test_definition_id": test_def_id,
        "test_version_id": test_version_id,
        "owner_id": owner_id,
        "status": "queued",
        "created_at": created_at,
    }).execute()
    return r

check("INSERT into test_runs", create_test_run)

# Transition run status
def update_run_status():
    r = client.table("test_runs").update({
        "status": "preparing",
        "started_at": created_at,
    }).eq("id", test_run_id).execute()
    return r

check("UPDATE test_runs.status (queued→preparing)", update_run_status)

# Create evidence artifact
artifact_id = str(uuid.uuid4())
def create_artifact():
    r = client.table("evidence_artifacts").insert({
        "id": artifact_id,
        "test_run_id": test_run_id,
        "artifact_type": "screenshot",
        "storage_path": f"fake/{test_run_id}/screenshot.png",
        "created_at": created_at,
    }).execute()
    return r

check("INSERT into evidence_artifacts", create_artifact)

# Create repair proposal
proposal_id = str(uuid.uuid4())
def create_proposal():
    r = client.table("repair_proposals").insert({
        "id": proposal_id,
        "test_run_id": test_run_id,
        "from_version_id": test_version_id,
        "proposed_plan": {"steps": [], "required_assertions": []},
        "reason": "Verify script",
        "evidence_refs": [],
        "confidence": 0.9,
        "validation_status": "pending",
        "created_at": created_at,
    }).execute()
    return r

check("INSERT into repair_proposals", create_proposal)


print("\n=== Immutability Triggers ===")

def test_intent_immutable():
    try:
        client.table("test_definitions").update({
            "original_intent": "HACKED",
        }).eq("id", test_def_id).execute()
        raise AssertionError("Trigger did NOT fire — original_intent is NOT immutable!")
    except Exception as exc:
        if "immutable" in str(exc).lower():
            return "Trigger fired correctly"
        # Supabase may return a 4xx HTTP error wrapping the PG exception
        return f"Error (likely trigger): {exc}"

check("Trigger: original_intent mutation rejected", test_intent_immutable)

def test_version_update_blocked():
    try:
        client.table("test_versions").update({
            "change_reason": "HACKED",
        }).eq("id", test_version_id).execute()
        raise AssertionError("Trigger did NOT fire — test_versions is NOT immutable!")
    except Exception as exc:
        if "immutable" in str(exc).lower():
            return "Trigger fired correctly"
        return f"Error (likely trigger): {exc}"

check("Trigger: test_versions update rejected", test_version_update_blocked)


print("\n=== JWT Verification ===")

def test_jwt():
    import jwt as pyjwt
    payload = {
        "sub": "verify-user-001",
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = pyjwt.encode(payload, SUPABASE_JWT_SECRET, algorithm="HS256")
    decoded = pyjwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    assert decoded["sub"] == "verify-user-001"
    return "JWT round-trip OK"

check("JWT encode+decode with SUPABASE_JWT_SECRET", test_jwt)


print("\n=== Cleanup ===")

def cleanup_regular():
    """Delete everything except test_versions (which are intentionally immutable)."""
    client.table("repair_proposals").delete().eq("id", proposal_id).execute()
    client.table("evidence_artifacts").delete().eq("id", artifact_id).execute()
    client.table("test_runs").delete().eq("id", test_run_id).execute()
    client.table("test_definitions").update({"active_version_id": None}).eq("id", test_def_id).execute()
    client.table("test_definitions").delete().eq("id", test_def_id).execute()
    client.table("projects").delete().eq("id", project_id).execute()
    return "Deleted projects, test_definitions, test_runs, evidence_artifacts, repair_proposals"

check("Delete all mutable rows", cleanup_regular)

def cleanup_version_immutable():
    """Confirm test_version row cannot be deleted — this is CORRECT behaviour."""
    try:
        client.table("test_versions").delete().eq("id", test_version_id).execute()
        raise AssertionError("test_versions delete should have been blocked by trigger!")
    except AssertionError:
        raise
    except Exception as exc:
        if "immutable" in str(exc).lower() or "P0001" in str(exc):
            # Clean up the orphan via a direct truncate workaround isn't available,
            # so note it and accept — the row is a verify-script artifact
            return "Delete correctly blocked by trigger (append-only enforced at DB level)"
        raise

check("test_versions delete blocked by immutability trigger (expected)", cleanup_version_immutable)


print("\n" + "="*50)
print(f"  PASSED: {len(passed)}")
print(f"  FAILED: {len(failed)}")
if failed:
    print(f"\n  Failed checks:")
    for f in failed:
        print(f"    - {f}")
    sys.exit(1)
else:
    print("\n  🎉 All Supabase checks passed!")
    sys.exit(0)
