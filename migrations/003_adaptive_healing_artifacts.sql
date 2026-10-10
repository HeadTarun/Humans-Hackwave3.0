-- Migration 003: Adaptive healing — update evidence_artifacts and repair_proposals
-- Self-Healing E2E Testing Platform
--
-- Changes:
--   1. Add 'repair_candidate' to evidence_artifacts.artifact_type CHECK constraint.
--      The auto-repair loop in run_orchestrator.py stores repair candidates as
--      artifacts via: artifact_store.store(run_id, "repair_candidate", ...)
--      Without this, Supabase will raise a CHECK constraint violation at runtime.
--
--   2. Add 'accessibility_snapshot' to evidence_artifacts.artifact_type CHECK.
--      Browser runner now captures Playwright accessibility tree (a11y JSON).
--      Adding now to avoid a 4th migration later.
--
--   3. Add index on repair_proposals(validation_status) for faster lookups
--      when filtering 'pending' proposals awaiting review.
--
--   4. Add index on test_runs(classification) for analytics queries.
--
-- Run via: supabase db push
-- ============================================================

-- Step 1: Drop old CHECK constraint on artifact_type
-- (Postgres requires DROP + re-add; ALTER COLUMN CHECK not supported)
ALTER TABLE evidence_artifacts
    DROP CONSTRAINT IF EXISTS evidence_artifacts_artifact_type_check;

-- Step 2: Re-add with new allowed values (original 6 + 2 new)
ALTER TABLE evidence_artifacts
    ADD CONSTRAINT evidence_artifacts_artifact_type_check
    CHECK (artifact_type IN (
        -- Original values (migration 001)
        'screenshot',
        'trace',
        'dom_snapshot',
        'console_log',
        'network_log',
        'report',
        -- Adaptive healing additions (migration 003)
        'repair_candidate',        -- JSON blob: {auto_repair_candidate, source, failed_step_index}
        'accessibility_snapshot'   -- JSON blob: Playwright a11y tree (bounded 10KB)
    ));

-- Step 3: Index on repair_proposals.validation_status
-- Used when polling for pending proposals awaiting human review
CREATE INDEX IF NOT EXISTS idx_repair_proposals_validation_status
    ON repair_proposals(validation_status);

-- Step 4: Index on test_runs(classification) for analytics queries
-- e.g. "how many LOCATOR_DRIFT failures in the last 7 days?"
CREATE INDEX IF NOT EXISTS idx_test_runs_classification
    ON test_runs(classification)
    WHERE classification IS NOT NULL;
