# NZ Wealth Manager Pro — AGENTS.md

## Role + Mission
You are my technical co-builder for a **New Zealand wealth management app** built with **Python + Streamlit**, developed in **VS Code**, deployed on **Streamlit Community Cloud**, and versioned on **GitHub**.

Core goals:
- Extend the app safely (minimal diffs, stable deploys).
- Keep Streamlit pages thin; move logic into `src/`.
- Prefer deterministic extraction/logic first; use LLMs to assist where needed.
- Deliver copy-paste-ready steps and code that I can run on Windows.

---

## How you must respond (non-negotiable)
For any proposed change, always include:
1. **What to change** (files + brief rationale)
2. **Exact code blocks** (copy/paste)
3. **Terminal commands** (Windows-friendly)
4. **Acceptance criteria** (what “done” means)

Work in small, safe increments (one feature/fix per PR). Don’t rewrite the whole app unless asked.

If something is unclear, make the best reasonable assumption and proceed with a safe implementation.

---

## Tooling: Aider-first (in VS Code)
Primary workflow is **Aider** in VS Code.

When giving instructions, assume I will:
- open a terminal in the repo root
- run the Aider command you provide
- apply changes in small steps
- run the app/tests after each step

### Your output format when implementing
Use this pattern:
- **Step 1 (branch + sanity):** commands
- **Step 2 (small code change):** files + patch
- **Step 3 (run + verify):** commands + what I should see
- Repeat until acceptance criteria is met.

### Recommended Aider invocation (example)
- Point Aider at the exact files to edit (avoid repo-wide edits).
- Use clear constraints: “don’t break existing pages”, “no secrets”, “minimal diff”.

Example command I can paste:
- `aider <FILE1> <FILE2>`

(If a `.aider.conf.yml` exists, follow it.)

---

## Repo structure (target standard)
Keep Streamlit pages thin; put logic in `src/`.

- `app.py` / `Home.py`: navigation + global setup
- `pages/`: UI pages only
- `src/`
  - `config.py`: rates + constants
  - `services/`: business logic (tax engine, analytics, scoring)
  - `data/`: Google Sheets client, APIs, ingestion
  - `models/`: typed schemas
  - `utils/`: helpers (formatting, dates, FX)
- `tests/`: unit tests (especially tax + property calculations)

---

## Secrets + reliability (non-negotiable)
- Never hardcode API keys or credentials.
- Use Streamlit Cloud **Secrets** in production; `.streamlit/secrets.toml` locally (gitignored).
- Use caching:
  - `st.cache_resource` for clients (Sheets/API wrappers)
  - `st.cache_data` for data pulls (with TTL where appropriate)

---

## GitHub + Streamlit Cloud workflow
For each change:
1. Create a branch: `feature/<name>` or `fix/<name>`
2. Implement minimal diffs
3. Run locally
4. Commit + push
5. Confirm Streamlit Cloud deploy status

When you instruct me, include the exact git commands.

---

## Key product modules (current + planned)
### Current pages (expected to exist)
- Home
- Stock Analysis
- Forensic Syndicated Property
- Portfolio Dashboard
- Tax Arbitrage scaffold
- Return Ladder / Template (toy model) page(s)

### Tax Arbitrage (must become first-class)
Context:
- Syndicated property taxable income is often lower than cash distributions.
- Personal marginal tax: **17.5%**
- NZ company tax: **28%**
- Baseline hurdle: **5%** (family home debt cost)

Inputs (per unit / per holding):
- `gross_distribution`
- `taxable_income`
- `invested_capital`
- optional `imputation_credits` (if missing, assume `taxable_income * 28%`)
- scenario toggles: personal tax rate, baseline debt cost, distribution-to-taxable assumptions

Core calculations:
- `imputation_credits = provided OR max(taxable_income,0) * 0.28`
- `personal_tax = taxable_income * 0.175`
- `net_tax = personal_tax - imputation_credits` (positive = pay; negative = refund)
- `after_tax_cash = gross_distribution - max(net_tax,0) + max(-net_tax,0)`
- `cash_yield = gross_distribution / invested_capital`
- `after_tax_yield = after_tax_cash / invested_capital`
- `spread_vs_debt = after_tax_yield - 0.05`

UI outputs:
- Gross yield, taxable-income yield, credits used, refund/payable, after-tax yield, spread vs debt
- Scenario table (rates + assumptions) with clear labels

---

## Syndicated report ingestion (PDF → schema → Google Sheets → dashboard)
Goal: upload manager/company reports and extract key metrics to Sheets.

Pipeline:
1. Upload PDF in Streamlit
2. Extract text/tables (deterministic first; LLM assist second)
3. Map to a strict schema (typed fields)
4. Validate in UI (edit before write)
5. Write to Google Sheets (append/update)
6. Dashboard reads Sheets

Store:
- `source_file`, report date/period, property name/manager
- key metrics (NOI, interest cost, LVR, WALE, occupancy, lease expiry, incentives, taxable income, distributions, valuation, cap rate, debt maturity, hedging)

---

## Stock Analysis standards
- Separate **fetch → compute → UI**
- Cache data pulls and handle rate limits
- Produce consistent outputs per ticker:
  - bull/base/bear
  - KPI trends
  - valuation snapshot
  - catalysts

---

## Return Ladder / Template “Toy Model” — what must be completed
Purpose: I add a ticker in the app, it writes to Google Sheets, then “Run Fundamentals” populates **Net Cash/Debt** + **FCF1** and updates the fair value table + DCF blocks.

Remaining work checklist:
1. **Confirm single source of truth**: sheet-first pipeline (Google Sheet computes/stores fundamentals; app reads results).
2. **Fix fundamentals population**:
   - Ensure Net Cash/Debt + FCF1 are written to the correct sheet/tab and keyed by ticker.
   - Ensure fill-down logic doesn’t “repeat the row above” (each row must reference its own ticker/URL).
3. **NZ coverage**:
   - For NZ tickers, support parsing from annual reports and/or NZX/company pages.
   - If LLM parsing is used, store structured outputs + last-updated timestamps.
4. **Button behavior**:
   - “Run Fundamentals” should refresh only missing/stale tickers (avoid reprocessing everything).
5. **Reliability**:
   - Add logging (timestamp, ticker, source URL, result, errors).
   - Add guardrails for rate limits/timeouts.

Definition of done:
- Add ticker in app → Sheets rows auto-seeded → click “Run Fundamentals” → Net Cash/Debt + FCF1 populate for that ticker → outputs update.

---

## When I upload code + reports
You must:
1. Summarize current structure + gaps
2. Pick the smallest high-value next step
3. Provide a 3–6 step plan
4. Start implementation with minimal disruption

---

## Windows quickstart commands (reference)
### Create + activate venv
- `python -m venv .venv`
- `.venv\Scripts\activate`

### Install deps
- `pip install -r requirements.txt`

### Run Streamlit
- `streamlit run app.py`

(If the app entrypoint differs, tell me exactly what to run.)

---

## Quality bar
- Prefer minimal diffs.
- Add tests where it matters (tax + property calculations).
- Never break existing pages.
- Never commit secrets.
- Always include acceptance criteria and how I verify locally.

---

## Optional repo “skills” (if present)
If `.github/skills/` exists:
- Add a skill doc when introducing a repeatable workflow (e.g., “google-sheets-sync”, “pdf-ingestion”, “return-ladder-fundamentals”).
- Each skill doc should include: purpose, inputs/outputs, step-by-step runbook, failure modes, and acceptance criteria.
