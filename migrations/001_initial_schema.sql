-- Migration: 001_initial_schema
-- Self-Healing E2E Testing Platform — Phase 1
-- Postgres + Supabase Auth + RLS
-- Run via Supabase dashboard SQL editor or CLI: supabase db push

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- PROJECTS
-- ============================================================
CREATE TABLE projects (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id      TEXT NOT NULL,             -- Supabase Auth uid()
    name          TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
    repo_metadata JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_projects_owner ON projects(owner_id);

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY projects_select ON projects
    FOR SELECT USING (owner_id = auth.uid()::text);
CREATE POLICY projects_insert ON projects
    FOR INSERT WITH CHECK (owner_id = auth.uid()::text);
CREATE POLICY projects_update ON projects
    FOR UPDATE USING (owner_id = auth.uid()::text)
    WITH CHECK (owner_id = auth.uid()::text);
CREATE POLICY projects_delete ON projects
    FOR DELETE USING (owner_id = auth.uid()::text);


-- ============================================================
-- TEST DEFINITIONS
-- Immutable fields: original_intent, required_assertions (enforced by trigger below)
-- ============================================================
CREATE TABLE test_definitions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    owner_id            TEXT NOT NULL,
    name                TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
    -- Write-once fields — trigger prevents mutation
    original_intent     TEXT NOT NULL CHECK (char_length(original_intent) BETWEEN 1 AND 5000),
    required_assertions JSONB NOT NULL,
    preconditions       JSONB,
    active_version_id   UUID,               -- FK added after test_versions table
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_test_definitions_project ON test_definitions(project_id);
CREATE INDEX idx_test_definitions_owner   ON test_definitions(owner_id);

ALTER TABLE test_definitions ENABLE ROW LEVEL SECURITY;

CREATE POLICY test_definitions_select ON test_definitions
    FOR SELECT USING (owner_id = auth.uid()::text);
CREATE POLICY test_definitions_insert ON test_definitions
    FOR INSERT WITH CHECK (owner_id = auth.uid()::text);
CREATE POLICY test_definitions_update ON test_definitions
    FOR UPDATE USING (owner_id = auth.uid()::text)
    WITH CHECK (owner_id = auth.uid()::text);
-- No delete policy: definitions are preserved for audit trail

-- Immutability trigger
CREATE OR REPLACE FUNCTION prevent_intent_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.original_intent IS DISTINCT FROM NEW.original_intent THEN
        RAISE EXCEPTION 'original_intent is immutable and cannot be changed';
    END IF;
    IF OLD.required_assertions IS DISTINCT FROM NEW.required_assertions THEN
        RAISE EXCEPTION 'required_assertions is immutable and cannot be changed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_prevent_intent_mutation
    BEFORE UPDATE ON test_definitions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_intent_mutation();


-- ============================================================
-- TEST VERSIONS (append-only — no UPDATE or DELETE)
-- ============================================================
CREATE TABLE test_versions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_definition_id  UUID NOT NULL REFERENCES test_definitions(id) ON DELETE CASCADE,
    version_number      INTEGER NOT NULL CHECK (version_number >= 1),
    action_plan         JSONB NOT NULL,
    assertions_snapshot JSONB NOT NULL,      -- Snapshot of required_assertions at creation
    repository_revision TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          TEXT NOT NULL,
    change_reason       TEXT NOT NULL CHECK (char_length(change_reason) BETWEEN 1 AND 1000),
    UNIQUE (test_definition_id, version_number)
);

CREATE INDEX idx_test_versions_definition ON test_versions(test_definition_id);

-- Add FK from test_definitions back to test_versions
ALTER TABLE test_definitions
    ADD CONSTRAINT fk_active_version
    FOREIGN KEY (active_version_id) REFERENCES test_versions(id);

ALTER TABLE test_versions ENABLE ROW LEVEL SECURITY;

-- Cascaded ownership: access granted if caller owns the parent test_definition
CREATE POLICY test_versions_select ON test_versions
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM test_definitions td
            WHERE td.id = test_versions.test_definition_id
              AND td.owner_id = auth.uid()::text
        )
    );
CREATE POLICY test_versions_insert ON test_versions
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM test_definitions td
            WHERE td.id = test_versions.test_definition_id
              AND td.owner_id = auth.uid()::text
        )
    );
-- No UPDATE or DELETE policies — versions are append-only

-- Prevent any update on test_versions (belt and suspenders)
CREATE OR REPLACE FUNCTION prevent_version_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'test_versions are immutable and append-only';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_prevent_version_update
    BEFORE UPDATE ON test_versions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_version_mutation();

CREATE TRIGGER trg_prevent_version_delete
    BEFORE DELETE ON test_versions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_version_mutation();


-- ============================================================
-- TEST RUNS
-- ============================================================
CREATE TABLE test_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_definition_id  UUID NOT NULL REFERENCES test_definitions(id) ON DELETE CASCADE,
    test_version_id     UUID NOT NULL REFERENCES test_versions(id),
    owner_id            TEXT NOT NULL,       -- Copied from test_definition for efficient RLS
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN (
                            'queued', 'preparing', 'running',
                            'collecting_evidence', 'classifying',
                            'repair_proposed', 'rerunning',
                            'passed', 'failed', 'needs_review', 'environment_failed'
                        )),
    classification      TEXT
                        CHECK (classification IN (
                            'LOCATOR_DRIFT', 'UI_FUNCTIONAL_REGRESSION',
                            'API_FAILURE', 'CONTRACT_MISMATCH',
                            'ASSERTION_FAILURE', 'ENVIRONMENT_FAILURE',
                            'AMBIGUOUS_REQUIRES_REVIEW'
                        )),
    confidence          NUMERIC(4,3) CHECK (confidence >= 0 AND confidence <= 1),
    summary             TEXT,
    environment_ref     TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    parent_run_id       UUID REFERENCES test_runs(id),  -- For repair reruns
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_test_runs_definition ON test_runs(test_definition_id);
CREATE INDEX idx_test_runs_version    ON test_runs(test_version_id);
CREATE INDEX idx_test_runs_owner      ON test_runs(owner_id);
CREATE INDEX idx_test_runs_status     ON test_runs(status);
CREATE INDEX idx_test_runs_parent     ON test_runs(parent_run_id);

ALTER TABLE test_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY test_runs_select ON test_runs
    FOR SELECT USING (owner_id = auth.uid()::text);
CREATE POLICY test_runs_insert ON test_runs
    FOR INSERT WITH CHECK (owner_id = auth.uid()::text);
CREATE POLICY test_runs_update ON test_runs
    FOR UPDATE USING (owner_id = auth.uid()::text)
    WITH CHECK (owner_id = auth.uid()::text);
-- No delete policy: run history is preserved


-- ============================================================
-- EVIDENCE ARTIFACTS (append-only)
-- ============================================================
CREATE TABLE evidence_artifacts (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_run_id   UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL
                  CHECK (artifact_type IN (
                      'screenshot', 'trace', 'dom_snapshot',
                      'console_log', 'network_log', 'report'
                  )),
    storage_path  TEXT NOT NULL,    -- Private Supabase Storage path (never a public URL)
    metadata      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_evidence_run ON evidence_artifacts(test_run_id);

ALTER TABLE evidence_artifacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY evidence_artifacts_select ON evidence_artifacts
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM test_runs tr
            WHERE tr.id = evidence_artifacts.test_run_id
              AND tr.owner_id = auth.uid()::text
        )
    );
CREATE POLICY evidence_artifacts_insert ON evidence_artifacts
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM test_runs tr
            WHERE tr.id = evidence_artifacts.test_run_id
              AND tr.owner_id = auth.uid()::text
        )
    );
-- No update/delete: evidence is append-only


-- ============================================================
-- REPAIR PROPOSALS
-- ============================================================
CREATE TABLE repair_proposals (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_run_id       UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    from_version_id   UUID NOT NULL REFERENCES test_versions(id),
    proposed_plan     JSONB NOT NULL,
    reason            TEXT NOT NULL,
    evidence_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence        NUMERIC(4,3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    validation_status TEXT NOT NULL DEFAULT 'pending'
                      CHECK (validation_status IN (
                          'pending', 'validating', 'validated_pass',
                          'validated_fail', 'approved', 'rejected'
                      )),
    validation_run_id UUID REFERENCES test_runs(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_repair_proposals_run ON repair_proposals(test_run_id);

ALTER TABLE repair_proposals ENABLE ROW LEVEL SECURITY;

CREATE POLICY repair_proposals_select ON repair_proposals
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM test_runs tr
            WHERE tr.id = repair_proposals.test_run_id
              AND tr.owner_id = auth.uid()::text
        )
    );
CREATE POLICY repair_proposals_insert ON repair_proposals
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM test_runs tr
            WHERE tr.id = repair_proposals.test_run_id
              AND tr.owner_id = auth.uid()::text
        )
    );
CREATE POLICY repair_proposals_update ON repair_proposals
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM test_runs tr
            WHERE tr.id = repair_proposals.test_run_id
              AND tr.owner_id = auth.uid()::text
        )
    );


-- ============================================================
-- SUPABASE PRIVATE STORAGE BUCKET
-- Run separately via Supabase dashboard or management API:
--
-- INSERT INTO storage.buckets (id, name, public)
--     VALUES ('test-artifacts', 'test-artifacts', false)
--     ON CONFLICT DO NOTHING;
--
-- Storage RLS: restrict access to owner_id-prefixed paths
-- Policy: allow read/write only on paths matching auth.uid()
-- (Configure via Supabase dashboard Storage > Policies)
-- ============================================================
