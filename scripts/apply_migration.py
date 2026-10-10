"""Apply a migration SQL file to Supabase.

Priority:
  1. Direct psycopg2 connection via DATABASE_URL or SUPABASE_DB_PASSWORD
  2. Supabase Management API (needs SUPABASE_ACCESS_TOKEN)
  3. Print SQL for manual paste into SQL Editor

Usage: .venv\Scripts\python.exe scripts\apply_migration.py migrations\002_...sql
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sql_file = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if not sql_file or not sql_file.exists():
    print(f"Usage: apply_migration.py <path_to_sql>")
    sys.exit(1)

sql = sql_file.read_text(encoding="utf-8")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PASSWORD  = os.environ.get("SUPABASE_DB_PASSWORD", "")
PAT          = os.environ.get("SUPABASE_ACCESS_TOKEN", "")

project_ref = SUPABASE_URL.replace("https://", "").split(".")[0] if SUPABASE_URL else ""

# --- Path 1: Direct psycopg2 via DATABASE_URL ---
if DATABASE_URL:
    import psycopg2
    print(f"Applying via DATABASE_URL...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql)
    conn.close()
    print(f"Migration applied: {sql_file.name}")
    sys.exit(0)

# --- Path 2: Direct psycopg2 via SUPABASE_DB_PASSWORD ---
if DB_PASSWORD and project_ref:
    import psycopg2
    # Supabase direct connection (port 5432) or pooler (port 6543)
    dsn = f"postgresql://postgres.{project_ref}:{DB_PASSWORD}@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    print(f"Applying via Supabase pooler (psycopg2)...")
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        # Execute each statement separately
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                cur.execute(stmt)
        conn.close()
        print(f"Migration applied: {sql_file.name}")
        sys.exit(0)
    except Exception as exc:
        print(f"Pooler connection failed: {exc}")
        print("Try adding the correct region to SUPABASE_DB_PASSWORD path in script.")

# --- Path 3: Management API with PAT ---
if PAT and project_ref:
    import httpx
    print(f"Applying via Supabase Management API...")
    resp = httpx.post(
        f"https://api.supabase.com/v1/projects/{project_ref}/database/query",
        headers={"Authorization": f"Bearer {PAT}", "Content-Type": "application/json"},
        json={"query": sql},
        timeout=30,
    )
    if resp.status_code == 200:
        print(f"Migration applied: {sql_file.name}")
        sys.exit(0)
    else:
        print(f"Management API failed ({resp.status_code}): {resp.text[:300]}")

# --- Path 4: Print for manual paste ---
print("\nNo automated migration path available.")
print("To apply manually, go to the Supabase SQL Editor:")
print(f"  https://supabase.com/dashboard/project/{project_ref}/sql/new")
print("\nPaste this SQL:\n")
print("=" * 60)
print(sql)
print("=" * 60)
print("\nOr add one of these to your .env:")
print("  DATABASE_URL=postgresql://postgres.<ref>:<password>@<host>:5432/postgres")
print("  SUPABASE_DB_PASSWORD=<your-postgres-password>")
print("  SUPABASE_ACCESS_TOKEN=<your-personal-access-token>")
sys.exit(0)
