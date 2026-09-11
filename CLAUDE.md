# NZ Wealth Manager Pro

Streamlit app tracking the Wilson family's assets: listed equities, ~30+ NZ syndicated
property investments, and personal assets. Google Sheets is the system of record.

Deployed on Streamlit Cloud; also run locally.

## Run

```bash
streamlit run Home.py
```

Requires either a local `credentials.json` (GCP service account, gitignored) **or**
`.streamlit/secrets.toml` with a `[gcp_service_account]` block. `ANTHROPIC_API_KEY` is
read from `st.secrets` when present, otherwise the Property Forensics page prompts for
it in a password field. Neither secret is in git.

## Structure

| Path | Role |
|---|---|
| `Home.py` | Entry point. Loads Sheets + CSVs into `st.session_state` (`stock_df`, `prop_df`, `personal_df`). Every page depends on this having run. |
| `pages/1_📈_Stock_Portfolio.py` | Equities view (442 lines). |
| `pages/2_📊_Portfolio_Dashboard.py` | Combined net-worth / cashflow view. |
| `pages/3_🏢_Property_Forensics.py` | Property dashboard + the AI PDF report scanner. |
| `modules/sheets.py` | Sheet keys, worksheet names, cached credentials, `open_*_worksheet()` helpers. |
| `modules/utils.py` | `clean_number`, `clean_percent`. |
| `modules/authentication.py` | `connect_to_sheet()` — opens by **name**. Unused by the live pages; supersede with `modules/sheets.py`. |
| `personal_assets.csv`, `portfolio.csv` | Local, committed. `portfolio.csv` looks like leftover sample data. |

**The session_state bridge**: pages open with a guard that calls `st.stop()` when
`prop_df` is missing, so a page cannot be loaded directly — Home must run first.

### Google Sheets

Keys and worksheet names live in `modules/sheets.py`. **Always open by key, never by
name** — a renamed or duplicated file in Drive must not be able to redirect a write.

- Stocks: `STOCKS_SHEET_ID`, tab `Clean_Stocks`.
- Property: `PROPERTY_SHEET_ID`, tab `Syndicate_Data`.

`Syndicate_Data` is flat: **one row per syndicate**, 22 columns A–V, no period/date
column. Columns K, L, M are unused placeholders. Syndicates are identified only by the
free-text `Entity_Name` in column A. `Owner_Entity == 'Gold Recovery Ltd'` separates
Bryn's holdings from the parents'.

## Gotchas

- **`clean_percent` divides by 100 when the value is > 2.0.** Fine for LVR and vacancy;
  it will silently destroy any ratio that legitimately exceeds 2 — an ICR of `2.5x`
  becomes `0.025`, a WALE of `4.2` years becomes `0.042`. Never route ICR, WALE, or
  payout-ratio values through it.
- `save_to_google_sheet` appends **positionally** — the 22-element list must stay aligned
  with the sheet's column order. Inserting a column in the sheet silently corrupts writes.
  Phase 2 replaces this with header-name writes.
- `test_connection.py` is empty. There are no tests.
- Four virtualenvs exist. **`.venvapp` is the live one** (anthropic 0.115.0, pydantic
  2.13.4, streamlit 1.52.2). `.venv`, `.venv312`, and `venv` are stale or broken and
  still carry the old `google-generativeai` dependency from the pre-Claude scanner.

## Current state of the AI extraction (as of 2026-09-11)

In `pages/2_🏢_Property_Forensics.py`, tab "Upload Report (AI Scanner)":

- Model `claude-haiku-4-5`, `messages.create`, `max_tokens=4096`.
- PDFs **are** passed as a `document` content block (base64 `application/pdf`) — not
  text-extracted first. This is correct and should be preserved.
- Output **is** schema-constrained via `output_config={"format": {"type": "json_schema", ...}}`
  with a hand-written dict schema.
- The prompt tells the model to *"Omit any field you cannot find"* — omission, not an
  explicit null, and no page references are requested.
- No prior-period row and no baseline are passed as context.
- Single file only (`st.file_uploader` without `accept_multiple_files`).

**Two dead ends to be aware of:**
1. `save_to_google_sheet()` is defined but **never called** from anywhere.
2. The scan result is written to `st.session_state['scanned_data']` and **never read**.

So extraction currently runs, costs money, and persists nothing. It also contains an
unreachable duplicated block (two copies of the append/except logic after an earlier
`return`).

## Planned work: quarterly report pipeline

Goal: when a quarter's investor reports all arrive at once, don't read everything —
structure everything, then read only what's flagged.

### Phase 0 — Foundations ✅ done
Pinned `anthropic==0.115.0` and added `pydantic==2.13.4`; renumbered Property Forensics
to `3_` to clear the duplicate `2_` prefix; removed the unreachable duplicated blocks in
`save_to_google_sheet`; centralised sheet keys and credentials in `modules/sheets.py` so
everything opens by key.

### Phase 1 — Schema-constrained extraction
`modules/schema.py` holding Pydantic models. Every field carries a value **and** a page
reference, and is explicitly `null` when absent. The model must never infer, estimate,
or annualise a figure.

Fields: valuation + valuation date, NTA per unit, total debt, facility expiry, LVR, ICR
actual, ICR covenant, swap/hedge expiries, occupancy, WALE, distribution rate, payout
ratio, adjusted operating profit vs forecast, cash, manager fees by category.

Feed `model_json_schema()` into `output_config.format`. Note: native PDF **citations are
incompatible with `output_config.format`** (the API returns 400), which is why page
references are modelled as ordinary schema fields the model fills in.

### Phase 2 — Storage
Two new tabs, written **by header name** rather than by position:
- `Syndicate_Periods` — one row per syndicate per period end.
- `Syndicate_Baseline` — one row per syndicate, from the IM: forecast distribution,
  formation NAV, covenant definitions, trust-deed thresholds.

Introduce a stable `syndicate_id` plus an alias list, since `Entity_Name` varies between
documents. Leave `Syndicate_Data` in place so the existing dashboards keep working.

### Phase 3 — Delta engine
`modules/deltas.py`, **plain Python, no LLM.** Compares each new period row against the
prior period and the baseline, emitting ranked flags with severity: distribution cut,
ICR headroom thinning, facility or swap expiry inside 18 months, occupancy drop, a
previously reported field now missing, manager fees up while distributions down. Unit
tested with pytest.

### Phase 4 — Batch intake
Upload a whole quarter at once, auto-match each PDF to a syndicate (filename + extracted
entity name against the alias list), loop extraction with per-file error isolation so one
bad document doesn't sink the run.

### Phase 5 — Narrative, last
Per-syndicate note and draft manager questions generated **only** from the stored row and
its flags — never from the PDF. This keeps the narrative traceable to structured data.

### Phase 6 — UI
New `pages/4_…_Quarterly_Review.py` following the existing session_state guard pattern.

## Conventions

- Claude API work: follow the `claude-api` skill. Default model `claude-opus-5`.
- Keep the `document` content block for PDFs — never pre-extract text.
- New numeric parsing for ratios must not reuse `clean_percent` (see Gotchas).
