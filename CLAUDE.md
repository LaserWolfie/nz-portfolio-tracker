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
| `pages/3_🏢_Property_Forensics.py` | Property dashboard + the ad-hoc single-document scanner. |
| `pages/4_🔎_Quarterly_Review.py` | The quarterly cycle: batch intake, ranked flags, draft notes. |
| `modules/ui.py` | Shared Streamlit helpers: the session_state guard, credential resolution, flag rendering. |
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

#### The grammar limit — read before adding a field

Structured outputs compile the JSON Schema into a decoding grammar, and that grammar has
a size ceiling. Exceed it and every request fails with a 400:

> The compiled grammar is too large, which would cause performance issues.

Measured, not guessed: **36 flat top-level fields is rejected; the same leaves nested
under 7 groups is accepted.** Stripping descriptions does not help (5,908-char schema
still rejected), and neither does dropping the list fields or the enum — it is driven by
leaf count and top-level property count, not by schema text size.

Two consequences, both load-bearing:

1. `SyndicateReport` is **grouped** — `identity`, `valuation`, `debt`, `tenancy`,
   `returns`, `conduct`. Add new fields *inside a group*, never at the top level. A test
   asserts the top level stays at or below 10 properties.
2. Extraction runs as **two passes** (`FinancialPass`, `AssetPass`), merged by
   `merge_passes()`. Even grouped, all 32 figures in one request exceeds the ceiling. The
   passes run **concurrently**, so wall time is unchanged (~42s) at twice the token cost
   (~$0.80/report). Each pass also gets a focused prompt naming the sections to
   concentrate on, which is a quality gain rather than only a workaround.

Walk figures with `schema.iter_figures(report)` / `schema.figure_names()` rather than
touching `model_fields` directly, so regrouping never silently drops a storage column.

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

### Phase 6 — UI ✅ done
`pages/4_🔎_Quarterly_Review.py`, following the session_state guard pattern.

The pages split along what you are actually doing:

- **Property Forensics** — the portfolio dashboard and the ad-hoc single-document
  scanner. It had grown to four tabs and ~580 lines; now two tabs and 343.
- **Quarterly Review** — batch intake on one tab, the ranked worklist and draft notes on
  the other. This is the page for when a quarter arrives.

`modules/ui.py` holds what both pages need — `require_portfolio_data()`,
`anthropic_credentials()`, `load_pipeline_data()`, `render_flag()` — so the guard and the
credential chain are written once. It is the only module that imports Streamlit alongside
pipeline logic; `extraction`, `deltas`, `storage`, `batch` and `narrative` stay headless
and testable.

Two things worth knowing about the page:

- Stored rows are read once at page load, so a batch saved on the intake tab is not
  visible on the flags tab until a re-run. Hence the explicit **Reload stored rows**
  button.
- Navigating straight to a page URL starts a fresh Streamlit session with empty
  `session_state`, so the guard fires. Reach the pages through the sidebar.

Verified in the running app against live data: the guard fires on direct access, Home
populates, Quarterly Review renders the Augusta row with both flags colour-ranked, and
Property Forensics still shows its dashboard.

## Conventions

- Claude API work: follow the `claude-api` skill. Default model `claude-opus-5`.
- Keep the `document` content block for PDFs — never pre-extract text.
- New numeric parsing for ratios must not reuse `clean_percent` (see Gotchas).

## Benchmarking (planned)

The goal is not only change detection but **comparison against the industry, and holding
managers to account for creating value**. Four benchmark sources, all wanted:

1. **The portfolio itself** — 30+ syndicates is its own peer set. Percentile ranks for
   cap rate, fee load, LVR, WALE, rent reversion and payout ratio need no external data,
   and because several syndicates share a manager, the same manager can be compared
   across their own funds.
2. **Listed NZ property vehicles** — Precinct, Argosy, Goodman, Kiwi Property, Property
   for Industry publish comparable metrics semi-annually.
3. **Valuer sector data** — CBRE / Colliers / JLL NZ cap rate and market rent series by
   sector and region.
4. **The IM and trust deed** — each manager against what they themselves forecast.
   `Syndicate_Baseline` already holds these columns.

Sources 2 and 3 are external reference series; both should land in one
`Industry_Benchmarks` tab keyed by (metric, sector, period, source) so the benchmarking
module reads one mechanism rather than two.

### Manager-accountability fields (added, extracted, verified)

- `capitalisation_rate_percent`, `discount_rate_percent`, `terminal_yield_percent` — the
  valuation assumptions, and the most directly comparable metrics against sector data.
- `net_market_rent_per_sqm` vs `net_passing_rent_per_sqm`, with derived
  `rent_reversion_percent`. **Augusta is let 28.4% above market** ($818 passing vs $637
  market) on a 3.35-year WALE, so rents fall as leases roll. Invisible to any
  period-on-period comparison.
- `capex_spent` — Augusta spent **$16,735** on a $115m property.
- `lease_incentives_paid`, `nbs_rating_percent`, `net_lettable_area_sqm`.
- `management_fee_escalation_basis` — Augusta's fee rises annually at "the greater of 3%
  or CPI", i.e. independently of performance.
- `related_party_transactions` — recorded as a statement, so an explicit "there were
  none" stays distinguishable from silence.

### Syndicate identity

`Syndicate_Baseline` was seeded by `scripts/scaffold_baseline.py` (dry-run by default,
`--write` to apply; re-runnable, only ever appends). 34 rows.

**`Syndicate_Data` is one row per HOLDING, not per syndicate.** The same syndicate appears
once per family entity that owns units in it, so "33 Broadway Trust", "St George Group"
and "Merx Wholeale Pie Trust 1" each appear twice. 36 holding rows collapse to 33
syndicates. Baseline is per syndicate, because LVR, cap rate and WALE are properties of
the syndicate; `owner_entity` holds the pipe-separated list of holders.

`syndicate_id` is `MANAGER-DISTINCTIVE`, e.g. `CENT-PENROSE`, `PMG-GENERATION`. Two
lessons are baked into `make_id()` and guarded by tests:

- **The manager belongs in the id.** Stripping it collapsed "Centuria Industrial Fund"
  and "Jasper Industrial Income Plus Fund" onto the same code.
- **A lone letter or digit is often the only distinguishing token** — Building A vs B,
  Govt Income 1 vs 2. Dropping it left the ids separated only by a collision suffix whose
  value depends on row order, and so was not stable.

The script **never merges two names on a guess**; look-alikes are reported for a human to
confirm. All four flagged pairs were confirmed and merged by
`scripts/merge_baseline.py` (dry-run by default, `--write` to apply), taking the baseline
from 34 rows to 30:

| Survives | Absorbed | Why |
|---|---|---|
| `CENT-BUILDINGB` | `CENT-GRAHAMB` | `Graham St "B"` is Building B, held by Group Reality |
| `CENT-WILLIAMSSTRE` | `CENT-WILLIAMST` | Cedenco is the Williams Street property |
| `ESKI-DAYCARE` | `ESKI-DAYCARE2` | One childcare fund, two holdings |
| `SGB` | `CENT-STGEORGE` | "St George Group" is Augusta St Georges Bay Road |

`SGB` survives rather than `CENT-STGEORGE` because the extracted period row was already
filed against it. **`Building A` and `Building B` Graham Street remain separate** — they
are genuinely different buildings.

### The same property can wear several names

A property appears more than once because it is **held by different family companies**, or
because a parcel was **bought later on the secondary market at a different cost basis**.
Neither makes it a different property.

This is why the earlier metric comparison was misleading. Where two rows for the same
property disagreed on LVR, WALE or ICR, that was **stale data on one row**, not two
buildings. The tell for a true duplicate is byte-identical property metrics — `33 Broadway
Trust`, `St George Group` and `Merx Wholeale` each show exactly that across their two
owner rows.

The consequence for anything that compares syndicates:

- **Property facts** — valuation, LVR, WALE, cap rate, occupancy, ICR, facility expiry —
  belong to the syndicate, and live in `Syndicate_Baseline` / `Syndicate_Periods`, once.
- **Holding facts** — `Original_Value` (a cost basis), `Current_Value`, the family's yield
  on cost — belong to the holding, and stay in `Syndicate_Data`, once per owner.

`Original_Value` is therefore **not** comparable across holdings as if it were a property
metric: two entries for one property can differ wildly and both be right. Note the related
subtlety for benchmarking — a `distribution_rate` quoted on *subscription price* is a
syndicate fact and is comparable across syndicates, but the family's own yield depends on
what each entity actually paid, so the two must never be mixed.

**Two syndicates have documents but no holding row**: "Sir William Pickering Drive
Limited Partnership" and "Westpoint Property Scheme" appear in Drive and Downloads but not
in `Syndicate_Data`, so they resolve to no match. Either add them or confirm they are
sold.

Typos in `Syndicate_Data` are preserved as canonical ("Wholeale", "Heathcare", "Eskine &
Owen" for Erskine & Owen) — it is the system of record, so aliases absorb the variance
rather than the source being silently corrected.

### Phase 4 — Batch intake ✅ done

`modules/batch.py`, with a "📦 Batch Intake (Quarter)" tab on Property Forensics.
`tests/test_batch.py` fakes extraction, so the wiring is tested without spending money.

The flow is **plan → extract → review → save**:

1. `plan_batch()` matches filenames to syndicates with **no API calls**, so a whole
   quarter can be checked, and its cost seen, before anything is spent. Extraction is the
   expensive step; matching is free.
2. `run_batch()` extracts up to `MAX_CONCURRENT_DOCUMENTS` (3) at once — each document
   already runs two concurrent passes, so that is six requests in flight.
3. `save_batch()` writes only what is ready.

Rules that matter:

- **Per-file error isolation.** Every document is extracted inside its own try/except,
  including a bare `except Exception`. One corrupt file must not sink the other
  twenty-nine.
- **The document outranks the filename.** A filename is a label someone typed; the entity
  name inside the report is evidence. On disagreement the extracted name wins, the item
  is marked `CONFLICT`, and it is **held back from saving** unless explicitly included.
- **Sparse documents are held back too.** Discovered live: a real tax statement matched
  its syndicate correctly and extracted 4 of 32 figures. Saved, it would have created a
  near-empty period row, and next quarter the delta engine would have read every field it
  omits as *disclosure withdrawn*. Below `SPARSE_THRESHOLD` (50%) an item is marked
  `SPARSE` and needs explicit inclusion.

`name_from_filename()` strips report words and **years only**. Stripping every digit made
`Govt Income 1` and `Govt Income 2` indistinguishable, and lost the `33` in `33 Broadway`.
`storage.LEGAL_SUFFIXES` also absorbs `LP`/`partnership`, so "Building B Graham Street LP"
matches "Building B Graham Street Limited Partnership".

Measured: two real documents, 22s wall clock, ~$1.60.

### Phase 5 — Narrative ✅ done

`modules/narrative.py` drafts a per-syndicate note and manager questions, plus a
"🔎 Quarterly Review" tab that ranks syndicates and drafts on demand.

**It never sees the PDF.** The document is read once, by the extractor, under a schema.
`build_facts()` is a pure function that assembles exactly what the writer may see, and it
is tested directly, so the guarantee does not rest on prompt wording. It excludes:

- `raw_json` and every `source_text` quote — verbatim prose lifted from the document,
  which would smuggle the PDF back in through the side door;
- `*_page` columns and the bookkeeping columns.

It includes current figures, prior period, IM baseline, the ranked flags, and a list of
what was **not disclosed** — because a null is a fact about the manager, not a zero.
`extraction_notes` is passed as `data_caveats_from_extraction`, labelled so it reads as a
caveat rather than as more figures.

`unsupported_numbers()` audits the draft: every number written must appear in the facts.
It is a cheap check for invention, not proof of correctness — a figure can be quoted
accurately and applied wrongly. Its tokeniser strips thousands separators **first**;
splitting `$115,000,000` on commas yields `115`, `000`, `000`, which made every large
figure look invented.

The prompt forbids new arithmetic outright — the delta engine already computed what
matters — and forbids investment advice.

On the real Augusta row it produced five specific, answerable questions, each with the
stored figure it rests on, including the rent-reversion and post-refinance-terms questions
that a human reading 30 reports would be unlikely to reach.
