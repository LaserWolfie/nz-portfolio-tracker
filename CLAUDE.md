# NZ Wealth Manager Pro

Streamlit app tracking the Wilson family's assets: listed equities, ~30+ NZ syndicated
property investments, and personal assets. Google Sheets is the system of record.

Deployed on Streamlit Cloud; also run locally.

## Run

```bash
streamlit run Home.py
```

Tests (pytest is a dev-only dependency, deliberately not in `requirements.txt`):

```bash
.venvapp/Scripts/python.exe -m pytest tests/ -q
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
- **Never append positionally.** The old `save_to_google_sheet` built a 22-element list
  and appended it by position, so inserting a column shifted every value into the wrong
  field. It was dead code and has been removed; use `storage.row_from_dict()`.
- `test_connection.py` is empty (the real tests live in `tests/`).
- Four virtualenvs exist. **`.venvapp` is the live one** (anthropic 0.115.0, pydantic
  2.13.4, streamlit 1.52.2). `.venv`, `.venv312`, and `venv` are stale or broken and
  still carry the old `google-generativeai` dependency from the pre-Claude scanner.

## State of the AI extraction

`pages/3_🏢_Property_Forensics.py`, tab "Upload Report (AI Scanner)", now calls
`modules.extraction.extract_report()` and renders every figure with its page and
supporting quote for review.

**PDFs are passed as a `document` content block and must stay that way** — never
pre-extract to text. Layout is meaning in these reports: figures sit in tables under
"Current / Prior / Forecast" headings, and flattening is what makes a model take the
wrong column.

Extractions are reviewed figure-by-figure with page and quote, then saved to
`Syndicate_Periods` under a resolved `syndicate_id`.

**Validated against a real document.** The Augusta St Georges Bay Road FY2026 annual
report (44pp) extracts 18/18 fields correctly in ~45s for roughly $0.40. Ground truth and
the traps that document contains are in `tests/fixtures/augusta_fy2026_ground_truth.md`;
the extraction itself is checked in as `augusta_fy2026_extracted.json` and used as a test
fixture, so flattening is exercised against real output.

**Field descriptions must not contradict themselves.** An earlier `adjusted_operating_profit`
description listed "adjusted net profit" as a synonym while also saying the field is not
"net profit". Two runs of the same document resolved that conflict differently — one
returned the figure, one returned null. Wording ambiguity shows up as run-to-run variance,
so treat a flapping field as a prompt bug, not model noise.

## Planned work: quarterly report pipeline

Goal: when a quarter's investor reports all arrive at once, don't read everything —
structure everything, then read only what's flagged.

### Phase 0 — Foundations ✅ done
Pinned `anthropic==0.115.0` and added `pydantic==2.13.4`; renumbered Property Forensics
to `3_` to clear the duplicate `2_` prefix; removed the unreachable duplicated blocks in
`save_to_google_sheet`; centralised sheet keys and credentials in `modules/sheets.py` so
everything opens by key.

### Phase 1 — Schema-constrained extraction ✅ done
`modules/schema.py` holds the Pydantic models; `modules/extraction.py` calls the API;
`tests/test_schema.py` covers the contract without spending tokens.

- `Figure` / `DateFigure` / `TextFigure` each wrap one value with `page` and a verbatim
  `source_text`. Native PDF **citations are incompatible with `output_config.format`**
  (the API returns 400), so provenance is modelled as ordinary schema fields.
- **No field carries a default.** Pydantic therefore marks all 23 as `required` while
  still allowing `null`, so the API itself enforces "explicit null, never omitted" —
  it does not rest on prompt wording. A test asserts this property.
- Extraction uses `client.messages.parse(output_format=SyndicateReport)`. The SDK's
  `lib/_parse/_transform.py` handles `$defs`/`$ref` and forces `additionalProperties:
  false`, so nested models are fine.
- `claude-opus-5`, adaptive thinking, `effort: high`. Accuracy dominates here: ~30 docs a
  quarter is low volume, and a wrong figure is worse than a slow one.
- `completeness()` reports how many figures came back populated — the tell for an
  image-only PDF or a misfiled document.

**Units are pinned in the schema**: percentages whole (45.0 = 45%), ratios as multiples
(2.5 = 2.5x), WALE in years. Tests guard the ICR case specifically.

### Phase 2 — Storage ✅ done
`modules/storage.py`. Both tabs exist in the property spreadsheet and are written **by
header name**; `tests/test_storage.py` covers the contract with a faked worksheet.

- `Syndicate_Periods` — one row per syndicate per period end, 59 columns.
- `Syndicate_Baseline` — one row per syndicate: `syndicate_id`, canonical name, aliases,
  and the IM-derived reference figures.

Key properties:

- **The period columns are derived from `SyndicateReport`**, so adding a schema field
  extends the sheet instead of silently dropping the new data. `ensure_worksheets()` adds
  missing headers and never reorders or removes existing ones.
- `row_from_dict()` orders values by header text and **raises on an unknown key**. A
  column inserted by hand shifts nothing; a schema field with no column is an error, not
  a silent drop.
- Writes are an **upsert on `(syndicate_id, period_end)`** — re-scanning a document
  overwrites that period rather than creating a second, conflicting history.
- Nulls are written as empty cells, never `0`. "Not disclosed" must not read back as a
  real zero.
- `effective_facility_expiry` prefers `post_balance_date_facility_expiry` where present,
  so a facility already refinanced does not trigger the expiry flag.
- Repeating groups (swaps, fees) are summarised into the few columns the delta engine
  needs; the full structure is preserved verbatim in `raw_json` (~4.7KB, well inside the
  50,000-character cell limit).

`resolve_syndicate_id()` matches a reported entity name against canonical names and
aliases, ignoring case, punctuation and legal suffixes, then falls back to containment.
**It returns `None` rather than guessing** when the match is ambiguous — filing a report
against the wrong syndicate corrupts two histories at once. The UI asks in that case.

### Pre-existing quarterly tabs

The property spreadsheet already holds ~15 quarterly tabs (`Q1 2021` … `Q3 2026`) with a
different shape: Tenant, Location, Manager, Owner, Original/Current Value, Annual $,
Annual Return. These are holding snapshots, not covenant forensics — no LVR, ICR, WALE or
facility expiry. They are **not** read or written by the pipeline. They are a candidate
backfill source for valuation and distribution history, but matching entities across 15
tabs is its own project. `Syndicate_Data` likewise stays untouched so existing dashboards
keep working.

### Phase 3 — Delta engine ✅ done
`modules/deltas.py`, **plain Python, no LLM** — every flag must be reproducible and
explainable from two stored rows plus a baseline. `tests/test_deltas.py` covers it.

`evaluate(current, prior, baseline, as_at)` runs ten rules and returns `Flag`s sorted
most serious first. `review_all(period_rows, baseline_rows)` reviews the latest period of
every syndicate and ranks them — that ranked list *is* the worklist.

`Severity` is an `IntEnum` so flags sort naturally. It defines `__format__` because
`IntEnum` otherwise inherits `int.__format__` and `f"{severity:<8}"` renders `3`.

Two invariants the tests defend hardest:

- **Blank is never zero.** An undisclosed figure comes back from Sheets as `''`. Read as
  `0.0` it would report a distribution cut to nil, an LVR of zero and a covenant breach,
  all from a manager who simply did not publish the number. `deltas._num()` returns
  `None` for blanks. This module deliberately does **not** use `utils.clean_number`
  (defaults to `0.0`) or `utils.clean_percent` (the ICR-destroying divide).
- **Compare like with like.** A distribution rate on subscription price is not comparable
  with one on closing equity. Where the basis changed, the engine emits
  `distribution_basis_changed` instead of a fictional cut, and the fees-vs-distributions
  rule declines to fire at all.

`_rule_facility_expiry` reads `effective_facility_expiry`, so a facility already
refinanced past balance date does not raise an alarm. Flagging Augusta's Westpac maturity
when it had already been refinanced to ASB for three years would discredit the whole
report.

`_rule_disclosure_withdrawn` is the flag the required-and-nullable schema exists to
enable: because every field is always present and explicitly null, "the manager stopped
reporting this" is distinguishable from "the extractor missed it".

Thresholds are module constants, not scattered literals.

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
