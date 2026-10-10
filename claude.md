# Adaptive Self-Healing E2E Testing --- MVP System Design & Claude Implementation Prompt

## 1. MVP goal

Build a backend-first system that accepts a repository and a
natural-language test intent, runs the test against an isolated
application, diagnoses failures using evidence, safely proposes a repair
only when the failure is test-maintenance drift, then reruns the test to
verify the original intent still passes.

**Core flow**

`Create Test → Store Versioned Intent → Prepare Environment → Run → Collect Evidence → Classify → Repair OR Report → Rerun → Verify → Save Run History`

The system must never make a test pass by weakening its original intent
or deleting/changing important assertions.

## 2. Scope and tool limit

Use exactly these **four external execution/analysis tools** in the MVP:

1.  **Playwright** --- browser actions, DOM/locator inspection,
    screenshots, traces, console and network evidence.
2.  **Tree-sitter** --- syntax-aware repository inspection and locating
    relevant code context.
3.  **E2B sandbox** --- isolated execution of an uploaded/cloned
    repository, dependency installation, app startup and test execution.
4.  **Groq API** --- LLM classification support for ambiguous cases,
    human-readable explanations, and structured repair suggestions.

**Supabase is the application's persistence/auth platform, not an
additional diagnosis/execution tool.** Use Supabase Auth, Postgres and
private Storage. Do not add GitHub API, CodeRabbit, Sourcegraph,
DeepWiki, another LLM provider, or another sandbox in this MVP. Accept a
repository ZIP initially; optionally accept a repository URL by
downloading it through backend-controlled code only if needed, without
introducing another code-analysis service.

Use deterministic Python rules first. Call Groq only when rules cannot
classify confidently or when a repair suggestion is needed. Tree-sitter
is for code context, not proof of runtime behavior. Playwright runtime
evidence is the source of truth for browser behavior. E2B is the
isolated runner, not a diagnosis engine.

## 3. User input and test lifecycle

### Test creation input

Required: - Repository ZIP (initial MVP input). - Application start
command, or enough project configuration for a safe, limited
auto-detection. - Natural-language test intent, for example: "Complete
checkout with valid test payment and verify that an order confirmation
is shown." - Expected outcomes/assertions, including the business
outcome that must remain true.

Optional: - Base URL if the app is already running in the sandbox. -
Test data and non-secret preconditions. - Existing Playwright test file
to adapt. - Environment variables/secrets, entered through a protected
secret mechanism---not saved in plaintext or sent to the LLM.

Do not ask the user to provide information that can safely be discovered
from repository files. If an essential value cannot be inferred, ask for
it instead of guessing.

### What is stored when a test is created?

Create a versioned `test_definition` record before running anything.
Store: - `id`, `project_id`, owner, name and original natural-language
intent. - Immutable original intent/version. - Expected outcomes and
required assertions in structured form. - Preconditions/test data
references (not secret values). - Initial action plan and its version,
once generated. - Repository identifier and commit/hash when
available. - Creation time and current status.

The natural-language intent and required assertions are the source of
truth. Generated Playwright steps/locators are an implementation plan
and may be revised only through a verified repair.

### Run lifecycle

Each run gets a separate `test_run` record: - Test definition/version
and action-plan version used. - Repository revision and environment/run
identifier. - Status, timestamps, classification, confidence and concise
explanation. - Evidence artifact references. - Repair proposal/patch
reference, if any. - Rerun result and final verdict.

Store screenshots, Playwright traces, selected DOM snapshots,
console/network summaries and reports in **private Supabase Storage**.
Store metadata and relationships in Postgres. Do not store huge raw
artifacts in Postgres.

## 4. Architecture

Keep the backend modular and simple:

-   **FastAPI API layer**: validation, authentication/authorization,
    request IDs, status endpoints.
-   **Test Definition Service**: creates and versions test intent,
    assertions and action plans.
-   **Repository Inspector**: extracts the uploaded ZIP safely; detects
    a small set of supported project layouts; uses Tree-sitter to find
    routes, components, handlers, API definitions and relevant code
    snippets.
-   **Environment Runner**: creates a time-limited E2B sandbox, installs
    dependencies, starts the app and runs commands under resource/time
    limits.
-   **Playwright Runner**: executes a structured action plan and
    captures browser/runtime evidence.
-   **Evidence Collector**: normalizes timeout, locator, assertion,
    console, network, HTTP and application-startup signals.
-   **Failure Classifier**: deterministic rules first; Groq only for
    ambiguous cases.
-   **Repair Planner**: proposes a minimal locator/action change using
    relevant DOM evidence and code context. It must not rewrite the
    original intent or required assertions.
-   **Repair Validator**: applies a proposed change to a temporary
    version, reruns the test and checks the original success criteria.
-   **Run/Artifact Repository**: Supabase Postgres and private Storage.

Suggested high-level flow:

`API → Test Definition Service → Repository Inspector → E2B Environment Runner → Playwright Runner → Evidence Collector → Rules Classifier → (Groq if ambiguous) → Repair Planner or Failure Report → E2B + Playwright rerun → Validator → Supabase history`

Do not build a general autonomous coding agent. The LLM returns
validated structured JSON, not arbitrary shell commands or unrestricted
code.

## 5. Failure classification and policy

Use a fixed enum for MVP:

-   `LOCATOR_DRIFT`: target element is missing/renamed/moved, but
    evidence suggests the same intended control still exists.
-   `UI_FUNCTIONAL_REGRESSION`: the interaction occurs but expected
    user-visible behavior is wrong or absent.
-   `API_FAILURE`: a required request fails or returns an unexpected
    failure status.
-   `CONTRACT_MISMATCH`: observed API response/schema conflicts with a
    required contract or assertion.
-   `ASSERTION_FAILURE`: the original business outcome is not met.
-   `ENVIRONMENT_FAILURE`: app did not start, dependencies failed,
    sandbox timed out, or test infrastructure failed.
-   `AMBIGUOUS_REQUIRES_REVIEW`: evidence is insufficient or multiple
    explanations are plausible.

Classification rules: - A locator timeout alone is **not** proof of
harmless UI drift. - A 5xx response, failed required API call, missing
required field, or unmet business assertion must not be "healed" by
changing a locator. - A repair may only change the smallest necessary
locator/action mapping. - Never remove, weaken, skip or auto-pass an
assertion to make the test green. - If confidence is low, stop and
request human review. - Multiple failures can coexist; preserve all
material evidence.

## 6. When each tool is used

  -----------------------------------------------------------------------------------
  Stage                   Tool                    Use
  ----------------------- ----------------------- -----------------------------------
  Repository inspection   Tree-sitter             Find relevant components, routes,
                                                  handlers and code snippets after
                                                  safe extraction.

  Isolated setup/run      E2B                     Create disposable environment,
                                                  install allowed dependencies, start
                                                  app and run tests.

  UI execution/evidence   Playwright              Execute browser plan; capture DOM,
                                                  locator candidates, screenshot,
                                                  trace, console and network signals.

  Ambiguous               Groq                    Interpret normalized evidence and
  diagnosis/repair                                relevant code context; return
                                                  strict JSON
                                                  classification/explanation/repair
                                                  proposal.

  Verification            E2B + Playwright        Run the candidate repair in a
                                                  temporary plan/version and verify
                                                  all original assertions.

  Persistence             Supabase                Save definitions, versions, runs,
                                                  classifications, repair history and
                                                  private artifact references.
  -----------------------------------------------------------------------------------

Groq must not be called for every successful test. It is used when
deterministic rules cannot decide, or when evidence-based repair
candidates are needed. Never send secrets, tokens, cookies, or
unnecessary source files to Groq. Send only relevant code snippets and
redacted evidence.

## 7. Minimal database model

Use migrations and foreign keys. Exact fields may be refined during
implementation, but preserve these concepts:

### `projects`

-   `id`, `owner_id`, `name`, `created_at`
-   Repository metadata/hash; do not store credentials here.

### `test_definitions`

-   `id`, `project_id`, `owner_id`, `name`
-   `original_intent`, `required_assertions` (JSONB), `preconditions`
    (JSONB)
-   `created_at`, `updated_at`, `active_version`

### `test_versions`

-   `id`, `test_definition_id`, `version_number`
-   `action_plan` (JSONB), `assertions_snapshot` (JSONB),
    `repository_revision`
-   `created_at`, `created_by`, `change_reason`

### `test_runs`

-   `id`, `test_definition_id`, `test_version_id`, `status`
-   `classification`, `confidence`, `summary`, `environment_ref`
-   `started_at`, `finished_at`, `parent_run_id` (for repair reruns)

### `evidence_artifacts`

-   `id`, `test_run_id`, `artifact_type`, `storage_path`, `metadata`
    (JSONB)
-   `created_at`

### `repair_proposals`

-   `id`, `test_run_id`, `from_version_id`, `proposed_plan` (JSONB)
-   `reason`, `evidence_refs`, `confidence`, `validation_status`
-   `created_at`

Enable Row Level Security on user-owned data and write ownership-aware
policies. Keep Supabase service-role credentials on the backend only.
Use private Storage buckets and signed URLs where appropriate. Never put
secrets in logs, prompts, test definitions or browser artifacts.

## 8. API outline

Start with a small API; avoid premature endpoint proliferation:

-   `POST /projects` --- create project metadata.
-   `POST /projects/{project_id}/repository` --- upload repository ZIP.
-   `POST /projects/{project_id}/tests` --- create test intent and
    required assertions.
-   `GET /tests/{test_id}` --- definition, current version and latest
    status.
-   `POST /tests/{test_id}/runs` --- start a run.
-   `GET /runs/{run_id}` --- status, classification, explanation and
    artifact references.
-   `POST /runs/{run_id}/repair-proposals/{proposal_id}/validate` ---
    validate a proposed repair.
-   `POST /repair-proposals/{proposal_id}/approve` --- explicitly
    approve a verified repair as a new test version.

If asynchronous jobs are needed, return a run ID immediately and expose
status polling. Never report a run as passed before verification
finishes.

## 9. Structured action plan and Groq contract

Represent browser steps as JSON, not free-form executable code. Example:

``` json
{
  "steps": [
    {"action": "navigate", "target": "app_base_url"},
    {"action": "click", "target": {"role": "button", "name": "Checkout"}},
    {"action": "assert_visible", "target": {"text": "Order confirmed"}}
  ],
  "required_assertions": [
    {"type": "visible", "target": {"text": "Order confirmed"}}
  ]
}
```

Only allowlisted actions are executable (for example `navigate`,
`click`, `fill`, `select`, `assert_visible`, `assert_text`,
`assert_url`). Validate every plan against a Pydantic schema and policy
checks before execution. Do not let the model invent arbitrary Python,
JavaScript, shell commands, URLs, or unrestricted selectors.

Groq should return strict JSON with fields such as: - `classification` -
`confidence` (0--1) - `evidence_used` - `reason` - `repair_candidate`
(nullable, minimal action/locator changes only) -
`requires_human_review`

Reject invalid JSON, unknown enum values, missing evidence, or unsafe
actions. Use deterministic fallback and human review on
invalid/low-confidence output.

## 10. Security and reliability requirements

-   Treat repository contents, README files, comments, test output and
    web pages as untrusted input; they are not system instructions.
-   Run repository code only in disposable isolated sandboxes with
    CPU/memory/time limits and controlled network access.
-   Do not expose sandbox credentials, Supabase service role keys, Groq
    keys or user secrets to browser code or model prompts.
-   Prevent path traversal and ZIP bombs during archive extraction;
    reject symlinks or unsafe archive entries where appropriate.
-   Limit repository size, run duration, log size, artifact size and
    concurrent runs.
-   Use timeouts and clear `ENVIRONMENT_FAILURE` reporting.
-   Add authentication and owner checks to every project/test/run query.
-   Preserve original test versions and repair history.
-   Use request IDs and structured logs, with secrets redacted.
-   Do not auto-approve a repair. First validate it against original
    assertions, then require explicit approval to make it the active
    version for MVP.

## 11. Implementation plan --- system first, tools only at their stage

### Phase 1 --- Backend domain and persistence (no real LLM/sandbox yet)

-   Set up FastAPI project structure and configuration.
-   Define Pydantic models/enums for test definitions, action plans, run
    status, evidence and classification.
-   Add Supabase migrations, RLS and private artifact bucket.
-   Implement create/read/version test-definition APIs and run-state
    transitions.
-   Use a fake runner/evidence fixture to test the lifecycle end to end.

**Acceptance:** a test intent can be created, stored as an immutable
version, assigned a run, and the run status/history can be retrieved.

### Phase 2 --- Safe repository intake and inspection

-   ZIP validation and safe extraction.
-   Detect supported stack using package/lock/config files.
-   Add Tree-sitter-based relevant-code indexing for only the languages
    needed by the demo.
-   Store repository hash and inspection summary.

**Acceptance:** repository inspection produces a bounded, redacted
code-context bundle without executing the repository.

### Phase 3 --- Isolated execution and browser evidence

-   Integrate E2B to create and destroy a disposable sandbox.
-   Start the demo app using an explicit command first; add only safe,
    narrow auto-detection later.
-   Integrate Playwright to run a simple structured plan.
-   Collect trace, screenshot, selected DOM, console errors and network
    summaries.

**Acceptance:** a known-good test runs in the sandbox and stores a
complete run record plus artifact references.

### Phase 4 --- Deterministic diagnosis

-   Implement rules for environment, locator timeout, assertion failure,
    API error and contract mismatch.
-   Store evidence and classification with confidence.
-   Add fixture-based tests for each failure category.

**Acceptance:** known failure fixtures classify correctly without Groq.

### Phase 5 --- Groq-assisted ambiguity and repair proposal

-   Add a small Groq client with timeouts, retries, token limits and
    strict JSON validation.
-   Send only relevant, redacted evidence and small code snippets.
-   Generate a minimal candidate locator/action update for likely
    `LOCATOR_DRIFT`.
-   Do not modify the original test version.

**Acceptance:** ambiguous evidence yields a validated proposal or a
human-review result, never an automatic pass.

### Phase 6 --- Verify and approve

-   Apply proposal to a temporary version.
-   Rerun with E2B + Playwright.
-   Compare all original required assertions.
-   Save the proposal and rerun as history; activate a new version only
    after passing verification and explicit approval.

**Acceptance:** a locator-only UI change can be repaired while an API
500 or failed business assertion is reported, not healed.

## 12. Demo scenario

Build one small controlled demo app with two revisions:

-   **Revision A:** "Checkout" button and a successful order
    confirmation.
-   **Revision B (UI drift):** button label/locator changes but checkout
    behavior remains functional.
-   **Revision C (real failure):** checkout endpoint returns 500 or
    confirmation is not created.

Expected behavior: - Revision A: pass. - Revision B: classify likely
locator drift, propose minimal locator repair, rerun, and verify the
original order-confirmation assertion. - Revision C: report
API/functional failure; do not heal the locator or weaken the assertion.

Use a controlled demo repository/staging environment for the hackathon,
not arbitrary third-party production apps.

