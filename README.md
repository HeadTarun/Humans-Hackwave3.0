# HealTest — Self-Healing E2E Testing Platform

## Local Setup (Phase 1)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

## Environment

Copy `.env.example` to `.env`. Phase 1 uses in-memory persistence so Supabase credentials are **not required** for local development or running tests.

```bash
copy .env.example .env
```

## Run the Server

```bash
uvicorn app.main:app --reload
```

API docs available at: http://localhost:8000/docs

## Run Tests

```bash
pytest -v
```

## Key Design Decisions (Phase 1)

- **In-memory persistence**: No database required. Same interface as the Supabase layer that ships in Phase 2.
- **Auth simulation**: Pass `X-User-ID: <any-string>` header to identify the caller. All ownership checks are enforced — only the token verification mechanism changes in Phase 2.
- **State machine**: Run status transitions are strictly enforced. Invalid transitions return HTTP 409.
- **Immutability**: `original_intent` and `required_assertions` are write-once. Test versions are append-only.

## Phase Overview

| Phase | What | Status |
|---|---|---|
| 1 | Domain, persistence interface, lifecycle APIs | ✅ Complete |
| 2 | Safe ZIP intake, Tree-sitter inspection, Supabase | 🔜 |
| 3 | E2B sandbox, Playwright browser runner | 🔜 |
| 4 | Deterministic failure classifier | 🔜 |
| 5 | Groq-assisted ambiguity + repair proposal | 🔜 |
| 6 | Repair verification + approval flow | 🔜 |
