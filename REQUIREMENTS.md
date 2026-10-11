# GhostRunner — Frontend Requirements Specification
## Version: 1.0 | Target: Hackathon Demo | Build Time: ~3 Hours

---

## 1. PROJECT OVERVIEW

**Product:** GhoostRunner — Intent-Aware Self-Healing E2E Testing Platform  
**Goal:** Build a minimal, demo-ready frontend that showcases the full self-healing loop:
> Create Project → Upload Repo / Connect GitHub → Create Test → Run Test → See Failure → AI Repair → Approve → Re-run Passes ✅

**Developer Count:** 1  
**Hard Deadline:** 3 hours (hackathon demo)  
**Deployment Target:** Vercel  

---

## 2. TECH STACK (LOCKED)

| Layer | Choice | Reason |
|---|---|---|
| Framework | **Next.js 14 (App Router)** | Vercel-native, SSR-ready |
| Language | **TypeScript** | Type safety with backend models |
| Styling | **Tailwind CSS** | Fast, no CSS files |
| Component Library | **shadcn/ui** | Copy-paste Radix components, hackathon-friendly |
| Icons | **Lucide React** | Ships with shadcn/ui |
| Data Fetching | **SWR** | Lightweight, Vercel library, auto-polling support |
| Auth | **Supabase Auth (Google OAuth)** | One-click Google login |
| State Management | **React Context + useReducer** | Built-in, no extra library |
| Theme | **Dark + Light toggle** | Via `next-themes` |
| Deployment | **Vercel** | `vercel --prod` and done |

### Install Command (copy-paste ready):
```bash
npx create-next-app@latest GhoostRunner-frontend --typescript --tailwind --app --src-dir --import-alias "@/*"
cd GhoostRunner-frontend
npx shadcn@latest init
npx shadcn@latest add button badge card dialog input label textarea toast separator skeleton
npm install swr @supabase/supabase-js @supabase/auth-helpers-nextjs next-themes lucide-react
```

---

## 3. ENVIRONMENT VARIABLES

Create `.env.local` at root:

```env
# Backend API
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# Supabase
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key

# GitHub OAuth (configured in Supabase dashboard)
# No extra env needed — Supabase handles it
```

> **Note for developer:** `NEXT_PUBLIC_API_BASE_URL` changes to production URL on Vercel. Add it in Vercel dashboard → Project Settings → Environment Variables.

---

## 4. AUTHENTICATION

### Strategy: Supabase Auth — Google OAuth

**Flow:**
1. User lands on `/login` → clicks "Continue with Google"
2. Supabase handles OAuth redirect
3. On success → redirect to `/projects`
4. `supabase.auth.getUser()` returns user object
5. `user.id` is used as `owner_id` in every API request header

**Auth Header (attached to ALL API calls):**
```
X-Owner-ID: {supabase_user.id}
```

**Protected Routes:** All routes except `/login` require auth. Use Next.js middleware.

**Files needed:**
```
src/
├── middleware.ts              ← Supabase session refresh + route protection
├── app/
│   ├── login/page.tsx         ← Google OAuth button only
│   └── auth/callback/route.ts ← Supabase OAuth callback handler
└── lib/
    └── supabase/
        ├── client.ts          ← createBrowserClient()
        └── server.ts          ← createServerClient()
```

**Login Page — only this UI:**
```tsx
// Clean centered card with:
// - GhoostRunner logo + tagline
// - "Continue with Google" button (Google icon + text)
// - No email/password fields
// - Dark background
```

---

## 5. API CLIENT

**File:** `src/lib/api.ts`

```typescript
// Base fetcher — attaches owner_id to every request
// Used by SWR as the fetcher function

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export async function apiFetch<T>(
  endpoint: string,
  ownerId: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Owner-ID": ownerId,
      ...options.headers,
    },
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Network error" }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
```

**Error Handling:** On API error → show `toast` with error message + retry button. No automatic retry — user clicks retry manually.

---

## 6. PAGES (5 Total)

### Routing Map:
```
/login                    → Login (Google OAuth)
/projects                 → Projects List + Create
/projects/[id]            → Project Detail + Tests + Repo
/tests/[id]               → Test Detail + Run History + Live Run
/runs/[id]                → Run Detail + Evidence + Repair Flow
```

---

### PAGE 1: `/login`

**Purpose:** Entry point for all users

**UI Elements:**
- Dark/light card centered on screen
- App name "GhoostRunner" + subtitle "Self-Healing E2E Tests"
- Single button: `Continue with Google` (Google icon via Lucide or inline SVG)
- Footer: "Powered by Supabase Auth"

**Behavior:**
- Calls `supabase.auth.signInWithOAuth({ provider: 'google' })`
- On success → auto-redirect to `/projects`

**No validation needed.** One button only.

---

### PAGE 2: `/projects`

**Purpose:** See all projects, create a new one

**UI Elements:**
- Top bar: "My Projects" title + `New Project` button (right side)
- Project cards grid (2 columns)
  - Each card: Project name, created date, "Open →" link
  - Loading: 2 skeleton cards
  - Empty state: "No projects yet. Create one!" with illustration
- `New Project` → opens `<Dialog>` modal

**Create Project Modal:**
- Input: Project Name (required, min 1 char, max 255)
- Button: "Create Project" (disabled while loading, shows spinner)
- On success: close modal, refresh list, show success toast
- On error: show error toast with retry button

**API Calls:**
```
GET  /projects           → list all projects for this owner
POST /projects           → create new project
```

**SWR Key:** `["/projects", ownerId]`

---

### PAGE 3: `/projects/[id]`

**Purpose:** Project home — repo connection, test list, create test

**Layout:** Full width, 2 sections stacked vertically

**Section A — Repository Connection:**

```
┌─────────────────────────────────────────────┐
│  📦 Repository                               │
│  ─────────────────────────────────────────  │
│  [Connect GitHub Repo]   [Upload ZIP]        │
│                                              │
│  ← if connected/uploaded, show:              │
│  Stack: Next.js 14 ✅                        │
│  Components: 23 | Routes: 12 | Files: 45     │
└─────────────────────────────────────────────┘
```

**GitHub Connect:**
- Input field: `github.com/owner/repo` (URL format)
- "Connect" button → stores as `repo_metadata.github_url` in project
- (NOTE: Actual GitHub code fetching is Phase 2 — for demo, just store URL)

**ZIP Upload:**
- Simple `<input type="file" accept=".zip">` button (not drag-and-drop — saves time)
- On select → `POST /projects/{id}/repository` as multipart/form-data
- On success → show inspection card (stack name, counts)
- On error → toast with message

**Section B — Tests:**
```
┌─────────────────────────────────────────────┐
│  Tests                     [+ New Test]      │
│  ─────────────────────────────────────────  │
│  [TestDefinitionCard]                        │
│  [TestDefinitionCard]                        │
│  Empty state: "No tests yet"                 │
└─────────────────────────────────────────────┘
```

**TestDefinitionCard:**
- Test name (bold)
- Intent text (truncated, 2 lines max)
- Status badge: active version (v1, v2, ...)
- `View & Run →` link to `/tests/[id]`

**New Test Modal:**
- Input: Test name
- Textarea: Original Intent (natural language description)
- Textarea: Required Assertions (JSON format with placeholder example)
- "Create Test" button

**API Calls:**
```
GET  /projects/{id}              → project detail + repo_metadata
GET  /projects/{id}/tests        → test list
POST /projects/{id}/repository   → ZIP upload (multipart)
POST /projects/{id}/tests        → create test
```

---

### PAGE 4: `/tests/[id]`

**Purpose:** Run tests, watch live status, see history

**THIS IS THE MAIN DEMO PAGE**

**Layout:**

**Top Section — Test Info:**
- Test name (large heading)
- Intent text (full, readable)
- Version badge: `v1` (gray) or `v2` (blue — updated by repair)
- Big CTA: `▶ Run Test` button (green, large)
  - Disabled + spinner while a run is in progress
  - Shows "Running..." text during active run

**Middle Section — Active Run Panel (appears when run triggered):**
```
┌─────────────────────────────────────────────┐
│  Current Run                                 │
│  ─────────────────────────────────────────  │
│  Status: [RUNNING ●] (live badge, pulses)    │
│                                              │
│  ◉ QUEUED → ◉ PREPARING → ● RUNNING         │
│    → ○ COLLECTING_EVIDENCE → ○ CLASSIFYING  │
│                                             │
│  Last updated: 2s ago                        │
└─────────────────────────────────────────────┘
```

Polling: `useSWR` with `refreshInterval: 2000` until terminal status.

**Bottom Section — Run History Table:**

| # | Date | Status | Classification | Duration | Action |
|---|------|--------|----------------|----------|--------|
| 3 | 10 Oct 09:30 | ✅ PASSED | — | 12s | View |
| 2 | 10 Oct 09:28 | ❌ FAILED | LOCATOR_DRIFT | 8s | View |
| 1 | 10 Oct 09:25 | ✅ PASSED | — | 11s | View |

- "View" → link to `/runs/[id]`
- Most recent run at top

**API Calls:**
```
GET  /tests/{id}               → test definition + current version
POST /tests/{id}/runs          → trigger run (body: { environment_ref: "demo", start_command: null })
GET  /tests/{id}/runs          → run history (SWR, refreshInterval: 5000)
GET  /runs/{active_run_id}     → live run status (SWR, refreshInterval: 2000, stop when terminal)
```

**Default run body (hardcoded for demo):**
```json
{
  "environment_ref": "demo-env",
  "start_command": null
}
```

---

### PAGE 5: `/runs/[id]`

**Purpose:** Full run detail — evidence, repair, approve

**THE REPAIR FLOW PAGE — KEY DEMO MOMENT**

**Layout — 3 sections:**

**Section A — Run Overview:**
- Large status badge (color per RunStatus)
- Classification chip (if failed) — e.g., `🔴 LOCATOR_DRIFT`
- Confidence bar: `87% confident`
- Summary text (from `run.summary` field)
- `← Back to Test` link

**Run Status Color Map (implement these exactly):**
```
queued              → gray
preparing           → blue
running             → blue + pulse animation
collecting_evidence → indigo
classifying         → purple
repair_proposed     → amber
rerunning           → blue + pulse animation
passed              → green
failed              → red
needs_review        → orange
environment_failed  → red (darker)
```

**Section B — Evidence Gallery (GET /runs/{id}/artifacts):**

```
┌──────────────────────────────────────────────┐
│  Evidence                                     │
│  ──────────────────────────────────────────  │
│  [Screenshot] [Screenshot] [Screenshot]       │
│   img lightbox on click                       │
│                                              │
│  Console Log:                                │
│  ┌────────────────────────────────────────┐  │
│  │ [ERROR] Cannot find element .submit    │  │
│  │ [INFO]  Page loaded in 1.2s            │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  (other artifact types: links only)          │
└──────────────────────────────────────────────┘
```

- `screenshot` → `<img>` with click-to-expand (simple CSS, no lightbox lib)
- `console_log` → `<pre>` with dark background, scrollable, max-height 200px
- `dom_snapshot`, `network_log`, `repair_candidate`, `accessibility_snapshot` → just a download link with signed URL

**Section C — Repair Panel (conditional — show only if `repair_proposed` status OR repair proposals exist):**

```
┌──────────────────────────────────────────────┐
│  🔧 Repair Proposal                 [AMBER]   │
│  ──────────────────────────────────────────  │
│  Reason: "Button selector has changed"       │
│  Confidence: 91%                             │
│                                              │
│  Proposed Action Plan:                       │
│  [JSON code block — action plan steps]       │
│                                              │
│  Validation Status: PENDING                  │
│                                              │
│  [Validate Repair]  [✅ Approve] [❌ Reject] │
└──────────────────────────────────────────────┘
```

**Approve Button Flow:**
1. User clicks `✅ Approve`
2. Confirmation modal appears: "Are you sure you want to approve this repair? This will create a new test version (v2)."
3. Confirm → `POST /repair-proposals/{pid}/approve`
4. Success → toast "✅ Repair approved! New version created." + page refresh
5. Error → toast with retry button

**Validate Button:**
- `POST /runs/{id}/repair-proposals/{pid}/validate`
- Updates `validation_status` field
- Refresh card after success

**Also show — manual repair creation:**
```
[+ Create Repair Proposal] button (only visible if status = failed)
→ Opens modal with:
   - Proposed Plan: JSON textarea
   - Reason: text input
   - Confidence: number slider (0-100)
   - Submit → POST /runs/{id}/repair-proposals
```

**API Calls:**
```
GET  /runs/{id}                              → run detail (SWR, refreshInterval: 2000 if not terminal)
GET  /runs/{id}/artifacts                    → evidence (SWR)
GET  /runs/{id}/repair-proposals             → proposals (SWR)
POST /runs/{id}/repair-proposals             → create manual proposal
POST /runs/{id}/repair-proposals/{pid}/validate  → validate
POST /repair-proposals/{pid}/approve         → APPROVE (with body: {})
```

---

## 7. SHARED COMPONENTS

**Priority order — build these first:**

### 7.1 `RunStatusBadge` ← Build first, used everywhere

```tsx
// Props: status: RunStatus
// Returns: <Badge> with correct color + icon + label
// Example: <RunStatusBadge status="failed" /> → red badge with X icon
```

### 7.2 `FailureCategoryChip`
```tsx
// Props: category: FailureCategory
// Returns: colored chip with human-readable label
// LOCATOR_DRIFT → amber "Selector Drift"
// UI_FUNCTIONAL_REGRESSION → red "UI Regression"
// API_FAILURE → orange "API Failure"
// CONTRACT_MISMATCH → purple "Contract Mismatch"
// ASSERTION_FAILURE → red "Assertion Failed"
// ENVIRONMENT_FAILURE → gray "Environment Error"
// AMBIGUOUS_REQUIRES_REVIEW → blue "Needs Review"
```

### 7.3 `RunTimeline`
```tsx
// Props: currentStatus: RunStatus
// Shows horizontal stepper: QUEUED → PREPARING → RUNNING → ... → PASSED/FAILED
// Current step: highlighted circle
// Past steps: filled green
// Future steps: gray outline
```

### 7.4 `RepairProposalCard`
```tsx
// Props: proposal: RepairProposal, runId: string, onApproved: () => void
// Shows: reason, confidence, proposed plan JSON, validate/approve/reject buttons
// Contains: confirmation modal for approve
```

### 7.5 `AppLayout` (Sidebar + Topbar)

```
┌─────────┬────────────────────────────────────────┐
│         │  [GhoostRunner Logo]      [Theme] [Avatar]  │
│  SIDE   ├────────────────────────────────────────┤
│  BAR    │                                         │
│         │          PAGE CONTENT                   │
│ • Dash  │                                         │
│ • Proj  │                                         │
│         │                                         │
└─────────┴────────────────────────────────────────┘
```

Sidebar links:
- Dashboard (icon: LayoutDashboard) → `/projects`
- Projects (icon: FolderOpen) → `/projects`
- (no more links needed for hackathon)

Topbar:
- App logo + name
- Theme toggle (sun/moon icon)
- User avatar + name (from Supabase auth)
- Logout button (icon only)

---

## 8. TYPE DEFINITIONS

**File:** `src/types/domain.ts`

Mirror backend enums exactly:

```typescript
export type RunStatus =
  | "queued" | "preparing" | "running" | "collecting_evidence"
  | "classifying" | "repair_proposed" | "rerunning"
  | "passed" | "failed" | "needs_review" | "environment_failed";

export type FailureCategory =
  | "LOCATOR_DRIFT" | "UI_FUNCTIONAL_REGRESSION" | "API_FAILURE"
  | "CONTRACT_MISMATCH" | "ASSERTION_FAILURE"
  | "ENVIRONMENT_FAILURE" | "AMBIGUOUS_REQUIRES_REVIEW";

export type ArtifactType =
  | "screenshot" | "trace" | "dom_snapshot" | "console_log"
  | "network_log" | "report" | "repair_candidate" | "accessibility_snapshot";

export type RepairValidationStatus =
  | "pending" | "validating" | "validated_pass"
  | "validated_fail" | "approved" | "rejected";

export interface Project {
  id: string;
  owner_id: string;
  name: string;
  repo_metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface TestDefinition {
  id: string;
  project_id: string;
  owner_id: string;
  name: string;
  original_intent: string;
  required_assertions: RequiredAssertion[];
  preconditions: Record<string, unknown> | null;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
  current_version: TestVersion | null;
}

export interface TestVersion {
  id: string;
  test_definition_id: string;
  version_number: number;
  action_plan: ActionPlan;
  assertions_snapshot: RequiredAssertion[];
  repository_revision: string | null;
  created_at: string;
  created_by: string;
  change_reason: string;
}

export interface TestRun {
  id: string;
  test_definition_id: string;
  test_version_id: string;
  owner_id: string;
  status: RunStatus;
  classification: FailureCategory | null;
  confidence: number | null;
  summary: string | null;
  environment_ref: string | null;
  started_at: string | null;
  finished_at: string | null;
  parent_run_id: string | null;
  created_at: string;
}

export interface EvidenceArtifact {
  id: string;
  test_run_id: string;
  artifact_type: ArtifactType;
  storage_path: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
  url: string | null;  // signed URL from backend
}

export interface RepairProposal {
  id: string;
  test_run_id: string;
  from_version_id: string;
  proposed_plan: ActionPlan;
  reason: string;
  evidence_refs: string[];
  confidence: number;
  validation_status: RepairValidationStatus;
  validation_run_id: string | null;
  created_at: string;
}

export interface ActionPlan {
  steps: ActionStep[];
  required_assertions: RequiredAssertion[];
}

export interface ActionStep {
  action: string;
  target: ActionTarget | string;
  value: string | null;
  timeout_ms: number;
}

export interface ActionTarget {
  role: string | null;
  name: string | null;
  text: string | null;
  url: string | null;
  selector: string | null;
}

export interface RequiredAssertion {
  type: string;
  target: ActionTarget | string;
  expected_value: string | null;
  description: string | null;
}
```

---

## 9. FOLDER STRUCTURE

```
GhoostRunner-frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx                   ← Root layout (ThemeProvider, Toaster)
│   │   ├── page.tsx                     ← Redirect to /projects
│   │   ├── login/
│   │   │   └── page.tsx                 ← Google OAuth login page
│   │   ├── auth/
│   │   │   └── callback/
│   │   │       └── route.ts             ← Supabase OAuth callback
│   │   ├── projects/
│   │   │   ├── page.tsx                 ← Projects list
│   │   │   └── [id]/
│   │   │       └── page.tsx             ← Project detail
│   │   ├── tests/
│   │   │   └── [id]/
│   │   │       └── page.tsx             ← Test detail + run
│   │   └── runs/
│   │       └── [id]/
│   │           └── page.tsx             ← Run detail + repair
│   │
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppLayout.tsx            ← Sidebar + topbar wrapper
│   │   │   ├── Sidebar.tsx
│   │   │   └── Topbar.tsx
│   │   ├── domain/
│   │   │   ├── RunStatusBadge.tsx       ← BUILD FIRST
│   │   │   ├── FailureCategoryChip.tsx
│   │   │   ├── RunTimeline.tsx
│   │   │   ├── RepairProposalCard.tsx
│   │   │   ├── ArtifactGallery.tsx
│   │   │   ├── TestDefinitionCard.tsx
│   │   │   └── RepoInspectionCard.tsx
│   │   └── forms/
│   │       ├── CreateProjectForm.tsx
│   │       ├── CreateTestForm.tsx
│   │       └── RepoUploadForm.tsx
│   │
│   ├── lib/
│   │   ├── api.ts                       ← apiFetch base function
│   │   ├── supabase/
│   │   │   ├── client.ts                ← Browser supabase client
│   │   │   └── server.ts                ← Server supabase client
│   │   └── utils.ts                     ← cn(), formatDate(), getDuration()
│   │
│   ├── hooks/
│   │   ├── useAuth.ts                   ← useUser() hook (Supabase)
│   │   └── useRunPolling.ts             ← SWR with conditional refreshInterval
│   │
│   ├── types/
│   │   └── domain.ts                    ← All TypeScript types (Section 8)
│   │
│   └── middleware.ts                    ← Supabase session + route protection
│
├── .env.local                           ← Local env vars
├── .env.example                         ← Committed env template
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

---

## 10. HACKATHON BUILD ORDER (3 Hours)

### ⏱ Hour 1 — Foundation (0:00 → 1:00)

| Task | Time | File |
|------|------|------|
| Project setup + install | 10 min | — |
| Supabase project + Google OAuth config | 15 min | Supabase dashboard |
| `.env.local` setup | 5 min | `.env.local` |
| `middleware.ts` (auth guard) | 5 min | `src/middleware.ts` |
| `lib/api.ts` (base fetcher) | 5 min | `src/lib/api.ts` |
| `types/domain.ts` (all types) | 10 min | `src/types/domain.ts` |
| Login page (Google button) | 10 min | `app/login/page.tsx` |

### ⏱ Hour 2 — Core Pages (1:00 → 2:00)

| Task | Time | File |
|------|------|------|
| `AppLayout` + `Sidebar` + `Topbar` | 15 min | `components/layout/` |
| `RunStatusBadge` component | 5 min | `components/domain/` |
| Projects list page | 10 min | `app/projects/page.tsx` |
| Create Project modal | 5 min | inside projects page |
| Project detail page (tests list) | 10 min | `app/projects/[id]/page.tsx` |
| Create Test modal | 10 min | inside project detail |
| ZIP upload (simple input) | 5 min | `RepoUploadForm.tsx` |

### ⏱ Hour 3 — The Demo Flow (2:00 → 3:00)

| Task | Time | File |
|------|------|------|
| Test detail page + Run button | 10 min | `app/tests/[id]/page.tsx` |
| `useRunPolling` hook (SWR) | 5 min | `hooks/useRunPolling.ts` |
| `RunTimeline` component | 10 min | `components/domain/RunTimeline.tsx` |
| Run detail page (status + summary) | 5 min | `app/runs/[id]/page.tsx` |
| `RepairProposalCard` + confirm modal | 10 min | `components/domain/RepairProposalCard.tsx` |
| Artifact gallery (screenshots + logs) | 10 min | `components/domain/ArtifactGallery.tsx` |
| Final testing + Vercel deploy | 10 min | `vercel --prod` |

---

## 11. API INTEGRATION QUICK REFERENCE

### SWR Usage Pattern (copy-paste template):

```typescript
import useSWR from "swr";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

// Inside component:
const { ownerId } = useAuth();

const { data, error, isLoading, mutate } = useSWR(
  ownerId ? [`/projects/${projectId}/tests`, ownerId] : null,
  ([url, id]) => apiFetch(url, id),
  { refreshInterval: 0 } // set to 2000 for live polling
);
```

### Run Polling Pattern:

```typescript
// Stop polling when run reaches terminal status
const TERMINAL = ["passed", "failed", "needs_review", "environment_failed"];

const { data: run } = useSWR(
  runId ? [`/runs/${runId}`, ownerId] : null,
  ([url, id]) => apiFetch(url, id),
  {
    refreshInterval: (data) =>
      data && TERMINAL.includes(data.status) ? 0 : 2000,
  }
);
```

### File Upload Pattern:

```typescript
async function uploadZip(file: File, projectId: string, ownerId: string) {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_BASE_URL}/projects/${projectId}/repository`,
    {
      method: "POST",
      headers: { "X-Owner-ID": ownerId },
      // Do NOT set Content-Type — browser sets multipart boundary automatically
      body: formData,
    }
  );

  if (!res.ok) throw new Error("Upload failed");
  return res.json();
}
```

---

## 12. GITHUB CONNECT (DEMO SCOPE)

> **⚠️ Scope note:** Full GitHub repo fetch + auto-test-generation is post-hackathon. For demo, GitHub connect = store the URL only.

**Demo implementation:**
1. User enters `github.com/owner/repo` URL in input field
2. Click "Connect" → `PATCH /projects/{id}` with `repo_metadata: { github_url: url }`
   - **NOTE:** Backend does not have PATCH /projects/{id} yet → workaround: just store locally or skip API call for demo
3. Show green "GitHub connected ✅" badge
4. This is enough for demo — audience sees the integration exists

---

## 13. THEME CONFIGURATION

**Using `next-themes`:**

```typescript
// app/layout.tsx
import { ThemeProvider } from "next-themes";

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem={false}
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
```

**Topbar toggle:**
```tsx
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

// Simple toggle button in Topbar
```

**Tailwind dark mode:** `darkMode: "class"` in `tailwind.config.ts`

---

## 14. ERROR HANDLING STANDARD

All API errors follow this pattern:

```tsx
// On error from useSWR or apiFetch:
toast({
  title: "Error",
  description: error.message,
  variant: "destructive",
  action: <Button size="sm" onClick={mutate}>Retry</Button>,
});
```

- Never show raw error stack traces to user
- Always provide retry action on error toast
- Loading states: use shadcn `<Skeleton>` components

---

## 15. OUT OF SCOPE (Post-Hackathon)

These are explicitly **NOT** required for the demo:

- ❌ Real GitHub repo fetch / code analysis via GitHub API
- ❌ Email/password auth or magic link
- ❌ Mobile/tablet responsive design
- ❌ Pagination (show first 50 items max)
- ❌ Search/filter on any list
- ❌ Test version history comparison
- ❌ Multiple projects/tests bulk actions
- ❌ User settings / profile page
- ❌ Notifications system
- ❌ Audit log / activity feed
- ❌ Team/organization features
- ❌ CI/CD integration UI
- ❌ Real-time WebSocket (SWR polling is sufficient)
- ❌ Unit tests for frontend code (backend already tested)

---

## 16. DEMO WALKTHROUGH SCRIPT

**Exact steps to demo (memorize this):**

```
1. Open app → Google login → lands on /projects
2. Click "New Project" → type "E-Commerce Checkout" → Create
3. Open project → Upload ZIP (or type GitHub URL)
4. See: "Next.js 14 detected | 23 components | 12 routes"
5. Click "+ New Test" →
   - Name: "Checkout Flow"
   - Intent: "User adds item to cart, proceeds to checkout, completes payment, lands on order confirmation page"
   - Save
6. Click "View & Run" on the test card
7. Click big green "▶ Run Test" button
8. Watch timeline animate: QUEUED → PREPARING → RUNNING → COLLECTING_EVIDENCE → CLASSIFYING
9. Status changes to ❌ FAILED
10. Classification: "🔴 LOCATOR_DRIFT" with confidence 87%
11. Summary: "Button selector .checkout-btn not found. Element moved."
12. Scroll down → See "🔧 Repair Proposal" card appear
13. Show proposed plan: updated selector using role+name instead of CSS
14. Click "✅ Approve Repair"
15. Confirmation modal → "Confirm"
16. Toast: "Repair approved! New version v2 created"
17. Timeline shows: RERUNNING → ✅ PASSED
18. "Self-healing complete! Test v1 → v2 automatically updated."
```

**Total demo time:** ~3-5 minutes

---

## 17. VERCEL DEPLOYMENT CHECKLIST

```bash
# 1. Push code to GitHub
git push origin main

# 2. Import project in Vercel dashboard
# → vercel.com/new → select repo

# 3. Add environment variables in Vercel:
# NEXT_PUBLIC_API_BASE_URL = https://your-backend.railway.app
# NEXT_PUBLIC_SUPABASE_URL = your_supabase_url
# NEXT_PUBLIC_SUPABASE_ANON_KEY = your_supabase_anon_key

# 4. Add your Vercel domain to:
# → Supabase Auth → URL Configuration → Redirect URLs
# → Backend CORS origins (CORS_ORIGINS env var)

# 5. Deploy
vercel --prod
```

---

*Document Version: 1.0 | Created: 2026-10-11 | Author: GhoostRunner Team*
