-- Migration 002: Fix test_versions delete trigger to allow FK cascade deletes.
--
-- The v001 trigger blocks ALL deletes on test_versions, including those
-- triggered by ON DELETE CASCADE from test_definitions. This prevents
-- cleaning up test data and parent-row deletion.
--
-- Fix: Allow cascade-triggered deletes (where test_definition_id row no
-- longer exists) while still blocking direct delete attempts.
-- In practice for the MVP we drop the delete trigger entirely and rely
-- on two layers that are already in place:
--   1. No RLS DELETE policy on test_versions (Supabase anon/user key
--      cannot delete versions via the API).
--   2. No DELETE endpoint in the application layer.
--   3. UPDATE trigger still blocks any mutation.
--
-- test_versions remain append-only from every API-accessible path.

DROP TRIGGER IF EXISTS trg_prevent_version_delete ON test_versions;

-- Confirm update trigger is still active (idempotent recreate)
CREATE OR REPLACE FUNCTION prevent_version_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'test_versions are immutable and append-only';
    END IF;
    RETURN OLD;  -- Allow DELETE (e.g. CASCADE from parent)
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Re-create update trigger only
DROP TRIGGER IF EXISTS trg_prevent_version_update ON test_versions;
CREATE TRIGGER trg_prevent_version_update
    BEFORE UPDATE ON test_versions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_version_mutation();
