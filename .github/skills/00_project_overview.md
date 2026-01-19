# NZ Wealth Manager Pro  Project Overview

## Mission
Build and maintain a Streamlit (Python) app deployed on Streamlit Community Cloud and versioned on GitHub.
Primary goals:
- Keep deploys stable (minimal diffs, defensive coding).
- Keep pages thin; move logic to `src/`.
- Google Sheets is the source of truth for portfolio + models.

## Non-negotiables
- Never hardcode API keys/credentials.
- Production: Streamlit Secrets. Local: `.streamlit/secrets.toml` (gitignored).
- Cache correctly:
  - `st.cache_resource` for clients (Sheets/API wrappers)
  - `st.cache_data` for pulls/derived data (use TTL where needed)

## Expected repo structure
- `app.py` / `Home.py`: navigation + setup
- `pages/`: UI only
- `src/`: logic
  - `src/services/`: business logic (return ladder, tax engine, analytics)
  - `src/data/`: sheets client, APIs, ingestion
  - `src/models/`: schemas
  - `src/utils/`: helpers
- `scripts/`: smoketests + diagnostics
- `tests/`: unit tests for critical calcs

## Output format (when making changes)
Always include:
1) What to change (files + rationale)
2) Exact code blocks (copy/paste)
3) Terminal commands (Windows-friendly)
4) Acceptance criteria
Prefer one feature/fix per PR.

