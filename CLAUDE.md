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

`Syndicate_Data` is flat: **one row per holding** (owner × syndicate; a later parcel bought
at a different cost gets its own row), 28 columns A–AB, no period/date column. Syndicates
are identified only by the free-text `Entity_Name` in column A. `Owner_Entity == 'Gold
Recovery Ltd'` separates Bryn's holdings from the parents'. **All money is NZ dollars.**

Every reader (`Home.py`, `scripts/scaffold_baseline.py`) looks columns up **by header
name**, which is why columns could be inserted and moved safely. Never address this tab by
column letter or position in code — the letters below changed four times on 2026-09-14.

| Col | Header | Meaning |
|---|---|---|
| A–C | `Entity_Name`, `Owner_Entity`, `Manager` | Identity. Typos preserved; see *Names* below. |
| D | `Units_Held` | Units, shares or interests, in the manager's own terms. **Blank = unknown, never 0.** |
| E | `Unit_Basis` | Dollars per unit the manager's rate is paid on, in the holding's own currency: $50,000 for a syndicate unit, $25,000 for Airpark (remaining equity after its 2021 capital return), **$1 for anything quoting cents per unit/share**. Not the price paid. |
| F | `Original_Value` | NZ$ actually paid — a holding fact, differs between parcels. AUD holdings converted at purchase. |
| G | `Current_Value` | NZ$. |
| H | `Original_Annual_Distribution` | The **IM/PDS forecast** rate on the unit, not a yield on cost — SGB is 7% × $50,000 = $3,500/unit even for units bought at $35,000. |
| I | `Current_Rate` | Latest declared rate as a fraction formatted `%`. Cents-per-unit rates are the same number as a % of $1 (4.25 cps = 4.25%). |
| J | `Currency` | `AUD` or `NZD`. |
| K | `FX_To_NZD` | Fixed **1.09** for AUD (user-set), 1.00 for NZD. |
| L | `Annual_Distribution` | **Current run rate in NZ$**; the dashboards sum it. Formula `=D*E*I*K` where all four inputs are known, typed otherwise. |
| M–O | `LVR_Percent`, `WALT_Years`, `Vacancy_Percent` | Fractions (0.44 = 44%); WALT is years. A whole-number percentage renders as ×100 on Property Forensics — a stray Williams Street row filed under Gold Recovery (which never owned it; the holding is Group Realty's, and the row was removed on 2026-09-14) was entered as `30.69` and showed **3069% LVR**. LVR is a property fact, so every holding of one syndicate carries the same value. |
| P–R | `Tax_Type`, `Status`, `Review_Pending` | `Tax_Type` is `PIE` on the two Merx rows only; the other two are empty. |
| S–AB | `Distribution_At_Risk` … `Payout_Ratio` | Sparsely filled. |

Rules for maintaining it (confirmed with the user, 2026-09-14):

- **`Annual_Distribution` is the current rate, not last year's average.** Check the
  report's subsequent-events note: Penrose paid 5.00 cps in FY26 but 4.25 cps from April
  2026, and the sheet showed the old figure for months. Not every change is in a report:
  St Georges Bay Road paid 6.75% through FY26 and **3.5%** now (September 2026), a cut known
  only from the user. Confirm current rates with the user, not only the latest annual report.
- **Only put the formula on a row whose units, basis, rate and FX are all filled.** A
  formula over a blank input evaluates to $0, and the holding silently drops out of income.
  To complete a row, fill the inputs and copy an existing formula down.
- **AUD holdings** are Centuria Govt Income 1 and 2, Centuria Grenfell St and Warrawong
  Plaza — the Location column on the quarterly tab says which. The rate is fixed rather than
  a live `GOOGLEFINANCE` lookup, which can briefly error and read as $0; change `FX_To_NZD`
  on those four rows when it is reviewed. `Centuria Industrial Fund` is the **NZ** fund
  (bank description `CENTURIA NZ INDUSTRI`), in NZD.
- **Merx pays a variable distribution** (the deed sets it at whatever holds NAV at $1).
  `Current_Rate` holds the user's running estimate — 11% as at September 2026, in line with the
  fund's trailing twelve months of monthly returns — so its formula works like any other row.
  Revisit the estimate against the monthly performance table rather than trusting it indefinitely.
- **Sold holdings are removed from this tab and recorded on the `Sales` tab.** The dashboards
  do not read `Status`, so a sold row left in place still counts toward income and net worth.
- **The hand-maintained quarterly tabs are often more current than this one**, and the two
  can disagree. Check both before trusting either.

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
tabs is its own project. `Syndicate_Data` is likewise **never written by the pipeline**; it
is maintained by hand and feeds the existing dashboards (its column layout and upkeep rules
are under *Google Sheets* above).

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

### Names: fix them in the baseline, never in `Syndicate_Data`

Your sheet and the manager's reports rarely agree on a name. `AIRWAYS soe` was recorded
under its **tenant**; every report calls the property `Sir William Pickering Drive Limited
Partnership`. The same will happen again.

**Do not rename rows in `Syndicate_Data`.** It is the system of record for holdings and
feeds the existing dashboards. `Syndicate_Baseline` exists to absorb naming variance:

```bash
python scripts/manage_aliases.py --list
python scripts/manage_aliases.py --rename CENT-AIRWAYSSOE "Sir William Pickering Drive Limited Partnership"
python scripts/manage_aliases.py --add CENT-PENROSE "Centuria Penrose LP"
```

`--rename` promotes the report's name to `canonical_name` and keeps your shorthand as an
alias, so both resolve. Nothing is written without `--write`.

**`syndicate_id` never changes.** It is an opaque key and `Syndicate_Periods` rows are
filed against it; changing it would orphan them. `CENT-AIRWAYSSOE` still reads as the
Airways holding even though its canonical name is now the Pickering Drive partnership —
that is fine, and better than breaking the link.

**Westpoint Property Scheme was sold.** It has no baseline row, so a Westpoint document in
a batch resolves to no match and is held back. That is the correct outcome, not a bug.

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

### What the Drive documents actually look like

Checked 28 real filenames from the per-syndicate Drive folders against the baseline. Two
findings changed the design.

**The reporting cycle is half-yearly, not quarterly.** Managers issue a
`Biannual Report` at **31 March** and **30 September**, plus an `FY Annual Report`. The
page is still called Quarterly Review, but expect two periods a year per syndicate.
`biannual` is in the filename noise list for that reason.

**Half the documents are not reports.** Of 28 files: 9 periodic reports, 5 supporting
(valuation updates), and **14 administrative** — proxy voting forms, meeting notices,
product disclosure statements, governing documents, SIPOs, meeting presentations. A proxy
form costs the same to extract as an annual report and yields nothing, so
`document_kind()` classifies on the filename and the intake tab skips administrative
documents by default. On that sample it halves the bill, $22.40 → $11.20.

Matching went 21/28 → **25/28** after adding aliases (`Sir William Pickering`,
`33 Broadway`, `Birch Ave`/`Birch Nominees` for `CENT-CARTERSBIRCH` — another
tenant-vs-property name) and expanding the noise list. The three remaining misses are all
correct: `Product_Disclosure_Statement.pdf` and `Governing_Document.pdf` contain no
syndicate name at all (the document's own entity name resolves those during extraction),
and Westpoint is sold.

Drive is organised as one folder per company, each holding one folder per syndicate. Note
`St Georges Bay Road` and `Augusta St Georges Bay Road` are separate folders for the same
syndicate — both resolve to `SGB`.

### Drive coverage — the real constraint

Swept the whole Drive tree. The structure is:

```
Property/
├── Proportional Property/
│   └── Gold Recovery/              <- the ONLY company folder
│       ├── Sir William Pickering Drive/   33 Broadway Trust/
│       ├── Augusta St Georges Bay Road/   St Georges Bay Road/
│       ├── Centuria Penrose Ltd/          Birch Nominees/
│       ├── Merx/                          Westpoint/ (sold)
│       └── Tax Statements/  2024 Reports/  Sales/  Purchases/ ...
└── MP Innovation Carpark/
```

**Only 7 of 30 syndicates have documents in Drive**, all under Gold Recovery. The other 23
— every holding of Cambridge, Group Reality and Roy Wilson — have no folder at all:

- *Cambridge*: Building A Graham Street, Centuria Airpark (Bendon), Centuria Industrial
  Fund, Surplus Brokers, VIP Pacific
- *Group Reality*: Centuria NZ Agricultural, Centuria NZ Diversified, Jasper Industrial,
  PMG Direct Office, PMG Generation, Vicky Street Nominees, Building B Graham Street,
  Williams Street Nominees
- *Roy Wilson*: Centuria Govt Income 1 and 2, Centuria Grenfell St, E+O Heathcare, E+O NZ
  Daycare, IDEAL Electrical, Ohanga, Pastoral House, Preston Road, Warrawong Plaza

Matching itself is no longer the constraint: **34 of 37 real filenames resolve**. The
misses are `Product_Disclosure_Statement.pdf` and `Governing_Document.pdf` (no syndicate
name in the filename at all — the document's own entity name resolves them at extraction),
and one to confirm by hand:

- `240729-MP-Medical-Inv-LP-Capital-Raise-Update-v2.pdf` sits in the **MP Innovation
  Carpark** folder but names "MP Medical Inv LP". Probably the same investment as
  `MACK-MPINNOVATION`; not aliased on a guess.

Note the `Merx/` folder already contains a hand-built review structure (`holdings/`,
`governing/`, `questions/`, `memos/`, `extracted/`, `statements/`, `REVIEW-METHOD.md`) —
prior art for this pipeline, worth reading before building the benchmarking module.

### The Disclose Register solves the coverage problem

<https://disclose-register.companiesoffice.govt.nz> — the statutory register for NZ managed
investment schemes. **Public, no login.** Search by scheme name, scheme number, manager
name or NZBN at `app.mbieregisters.govt.nz/disclose/ui/start/searchSchemes`.

For `AUGUSTA ST GEORGES BAY ROAD PROPERTY TRUST (SCH12448)` the Documents tab holds:

| Section | Document |
|---|---|
| Financial Statements | `SGBR_-_FY26_Annual_Report.pdf` (2.3MB, uploaded 29 Jun 2026) |
| Other Documents → Annual report | the same file |
| Governing | `TrustDeed-AugustaStGeorgesBayRoadPropertyTrust.pdf` (2.5MB) |
| SIPO | `SIPOSTGEORGESBAYROADPROPERTYTRUST.pdf` |
| Manager and Supervisor | consent and certificate |

That FY26 annual report is the **same document** already extracted from Downloads. So the
register supplies, for free and without a login, the one report a year that matters — for
every registered scheme, including the 23 with nothing in Drive.

The **Trust Deed and SIPO** matter just as much: they are the source for the
`Syndicate_Baseline` covenant and trust-deed columns, which are still empty.

Three things to know:

- **Annual only.** `Interim scheme financial statement(s)` is "Not specified" for Augusta,
  so the half-yearly reports are not on the register. One period a year from this source;
  the biannuals still have to come from the manager or Drive.
- **Scheme numbers are a better key than names.** `SCH12448` is official, stable and
  external. Worth a `scheme_number` column on `Syndicate_Baseline` — it sidesteps name
  matching entirely, and the register's own scheme name is the best `canonical_name`.
  Already identified: `33 BROADWAY TRUST` SCH11912, `AUGUSTA ST GEORGES BAY ROAD PROPERTY
  TRUST` SCH12448, `AIRPARK NOMINEES JOINT VENTURE` SCH11740 (our "Centuria Airpark
  (Bendon)"), `BIRCH NOMINEES JOINT VENTURE` SCH11560, `PMG DIRECT OFFICE FUND` SCH10921.
- **Document links are JavaScript, not plain URLs.** Bulk collection means driving the
  browser per scheme rather than fetching a list of hrefs.

**`PMG GENERATION FUND` (SCH12827) shows as `Cancelled` on the register**, while
`PMG DIRECT OFFICE FUND` (SCH10921) is `Registered`. A cancelled registration usually means
wound up or restructured. **Resolved 2026-09-14:** PMG Generation merged into **Pacific Property Fund
Limited** on 31 July 2026, and Group Realty's 50,000 units became 41,098 PPF shares. The baseline keeps
`syndicate_id` `PMG-GENERATION` with the new canonical name and the old one as an alias.

**Centuria's own website needs a login.** Credentials are not entered on the user's behalf,
so those reports have to be downloaded by hand or shared into Drive.

### All 30 looked up on the Disclose Register

`Syndicate_Baseline` gained `scheme_number`, `register_name` and `register_status`.
**12 of 30 are Registered, 1 is Cancelled, 17 are not on the register at all.**

| syndicate_id | Scheme | Status |
|---|---|---|
| `SGB` | SCH12448 AUGUSTA ST GEORGES BAY ROAD PROPERTY TRUST | Registered |
| `CENT-BROADWAY33` | SCH11912 33 BROADWAY TRUST | Registered |
| `CENT-AIRPARKBENDO` | SCH11740 AIRPARK NOMINEES JOINT VENTURE | Registered |
| `CENT-CARTERSBIRCH` | SCH11560 BIRCH NOMINEES JOINT VENTURE | Registered |
| `CENT-BUILDINGA` | SCH10571 BUILDING A GRAHAM STREET LP | Registered |
| `CENT-BUILDINGB` | SCH10922 BUILDING B GRAHAM STREET LP | Registered |
| `CENT-DIVERSIFIED` | SCH12900 CENTURIA NZ DIVERSIFIED PROPERTY FUND | Registered |
| `CENT-AIRWAYSSOE` | SCH12323 SIR WILLIAM PICKERING DRIVE LP | Registered |
| `CENT-WILLIAMSSTRE` | SCH11570 WILLIAMS STREET NOMINEES JV | Registered |
| `OYST-VIP100` | SCH11688 100 HARRIS PROPORTIONATE OWNERSHIP SCHEME | Registered |
| `OYST-PASTORALHOUS` | SCH12806 PASTORAL HOUSE PROPORTIONATE OWNERSHIP SCHEME | Registered |
| `PMG-OFFICE` | SCH10921 PMG DIRECT OFFICE FUND | Registered |
| `PMG-GENERATION` | SCH12827 PMG GENERATION FUND | **Cancelled** — merged into Pacific Property Fund Limited, 31 Jul 2026 |

The register also confirms `WESTPOINT PROPERTY SCHEME` (SCH11602) is **Cancelled**,
independently corroborating that it was sold.

**Two register lookups corrected our names.** `Centuria Airpark Nominees (Bendon)` is
officially `AIRPARK NOMINEES JOINT VENTURE`, and `Carters Birch Nominees Joint Venture` is
officially `BIRCH NOMINEES JOINT VENTURE` — both recorded under a tenant or a variant.
`CENTURIA NZ DIVERSIFIED PROPERTY FUND` has also been renamed twice (`AUGUSTA PROPERTY
FUND` → `CENTURIA NZ PROPERTY FUND` → current), which is exactly the kind of drift the
alias list exists to absorb.

**The 17 absences are explainable, not search failures**, and they fall into four groups:

- **Australian assets**, outside the NZ regime entirely: `Centuria Grenfell St` (Adelaide),
  `Jasper Warrawong Plaza` (NSW). *`Centuria Industrial Fund` was originally listed here as an
  ASX-listed REIT. That was wrong: bank records (`CENTURIA NZ INDUSTRI`) show it is the NZ
  fund, so it should be searched on Disclose under its NZ name — not yet done.*
- **Not a scheme**: `Centuria Penrose Ltd` is a limited company, so it belongs on the
  Companies Register, not Disclose.
- **Managers with no registered schemes at all**: Erskine & Owen, Jasper, MacKersey, Merx
  (private debt), My Farm, Silver Fern. Likely wholesale or private offers outside the
  retail disclosure regime.
- **Unresolved**: Centuria NZ Agricultural, Centuria Govt Income 1 and 2, Vicky Street
  Nominees, PMG Preston Road. Worth a manual check — they may sit under a different legal
  name.

**What this means for document coverage.** The register supplies annual reports for the 12
Registered schemes, which together with Drive gets most of the way. The remaining ~17 have
to come from the manager directly — Centuria's login, or email.

### The Companies Register is a second public source

Chasing the five unresolved names turned up a source the Disclose Register does not cover.
`app.companiesoffice.govt.nz` — public, no login — and **NZ companies file their annual
financial statements there**:

| Entity | Number | Financial statements filed |
|---|---|---|
| `CENTURIA PENROSE LIMITED` | 8149464 | FY2022, FY2023, FY2024, FY2025, FY2026 (1.4–1.9MB each) |
| `CENTURIA NZ AGRICULTURAL PROPERTY FUND LIMITED` | 8616868 | FY2024, FY2025, FY2026 |

So a holding being a **company rather than a scheme is not a dead end** — it just files in a
different place. Both of these were previously marked "not on the register"; both in fact
have five and three years of statements respectively.

**This likely extends much further.** Limited partnerships have their own public register
too, so `IDEAL Electrical - Montreal`, `MP Innovation`, the Jasper and Erskine & Owen
vehicles and others may all be reachable. Worth checking entity by entity before concluding
any holding is undocumentable.

Two caveats on what these documents contain. Company financial statements are **statutory
accounts**, not investor reports: expect valuation, debt and covenant notes, but not the
key-information-summary tiles (LVR, WALE, cap rate as headline figures) that the schema was
built around. Extraction will be **thinner and may trip `SPARSE_THRESHOLD`** — that is the
check working, not failing.

### The remaining four

- `CENT-VICKYSTREET` — **resolved 2026-09-14.** The manager's June 2026 financial statements are
  titled `VICKERY STREET PROPERTY SCHEME`, so the holding is **Vickery Street Nominees Joint
  Venture** — not Vickers Road (SCH11556). It is not on Disclose under "Vickery" either. The
  baseline's canonical name is now the Vickery form, with the sheet's "Vicky Street" kept as an
  alias.
- `CENT-GOVT1`, `CENT-GOVT2` — nothing on Disclose under Centuria or "Government".
  **Confirmed Australian (AUD)** by the user on 2026-09-14, alongside Grenfell St and
  Warrawong Plaza. Govt Income 2 is **Centuria Government Income Property Fund No. 2**
  (ARSN 653 978 753, 25 Nile Street, Port Adelaide; units A$1.00, PDS forecast 5.25% FY22).
  Govt Income 1 is not yet identified.
- `PMG-PRESTONROAD` — PMG has only five schemes on Disclose and Preston Road is not among
  them. Likely a property held *inside* a PMG fund rather than a scheme in its own right.
  **Sold** — removed from `Syndicate_Data` and recorded on the `Sales` tab (2026-09-14).

### Final coverage: three registers swept

| Register | Holds | Use |
|---|---|---|
| **Disclose** `disclose-register.companiesoffice.govt.nz` | Annual financial statements, trust deed, SIPO | **12 syndicates** |
| **Companies** `app.companiesoffice.govt.nz` | Annual financial statements, for companies required to file | **3 syndicates** (Penrose FY22–26, Agricultural FY24–26, Centuria NZ Industrial Fund FY19–26, company 6806353) |
| **Limited Partnerships** `lp-register.companiesoffice.govt.nz` | **Annual returns only — no financial statements** | Identity and status only |

The LP register was checked directly: `NZ DAYCARE PROPERTIES FUND LP` (50065049) lists five
annual returns and its registration, and nothing else. **LPs and trusts are a dead end for
documents** — they confirm an entity exists but file no accounts publicly. Do not spend more
time there.

**15 of 30 syndicates have documents obtainable from public sources** — Centuria NZ Industrial
Fund was added on 2026-09-14 via the Companies Register. The remaining 15 must come from the
manager — Centuria's login, or email:

- *Australian, outside the NZ regime*: Centuria Grenfell St (Adelaide), Jasper Warrawong Plaza
  (NSW), and Centuria Govt Income 1 and 2 (all four confirmed AUD). Centuria Industrial Fund
  was listed here in error — it is the NZ fund, and files its accounts on the Companies Register.
  The Australian funds send investors PDSs and fund updates rather than filing publicly;
  Grenfell's June 2026 update is the source for its FY27 rate.
- *NZ but privately reported*: E+O Heathcare, E+O/NZ Daycare, IDEAL Electrical, Jasper
  Industrial, MP Innovation, Merx, Ohanga, Surplus Brokers, PMG Preston Road.
- *Cancelled*: PMG Generation Fund — merged into Pacific Property Fund Limited on 31 July 2026.
  Historic filings may still sit on Disclose under SCH12827; PPF itself has not been looked up.

That is the ceiling for free document collection. Benchmarking across the full 30 is not
achievable from public sources alone; benchmarking across the ~14 is.

### FY2026 reports pulled from the registers

11 files, 27.5 MB, in `C:\Users\Reforged\Downloads\register_reports\` — covering **10
syndicates not already in Drive**. Together with the 4 already there (Augusta SGB, 33
Broadway, Birch Nominees, Sir William Pickering) that is the full 14.

| File | Syndicate | Pages |
|---|---|---|
| `Airpark_-_FY26_Annual_Report.pdf` | `CENT-AIRPARKBENDO` | 40 |
| `Building_A_-_FY26_Annual_Report.pdf` | `CENT-BUILDINGA` | 40 |
| `Building_B_-_FY26_Annual_Report.pdf` | `CENT-BUILDINGB` | 40 |
| `CNZDPF_-_FY26_Annual_Report.pdf` | `CENT-DIVERSIFIED` | 35 |
| `Williams_St_-_FY26_Annual_Report.pdf` | `CENT-WILLIAMSSTRE` | 40 |
| `100_Harris_POS_-_FY26_Annual_Report.pdf` | `OYST-VIP100` | 36 |
| `Pastoral_House_POS_-_FY26_Annual_Report.pdf` | `OYST-PASTORALHOUS` | 36 |
| `PMG_Direct_Office_Fund_-_FY26_Financial_Statements.pdf` | `PMG-OFFICE` | 36 |
| `PMG_Direct_Office_Fund_-_FY26_Annual_Report_Reg62.pdf` | `PMG-OFFICE` | 6 |
| `Centuria_NZ_Agricultural_Property_Fund_-_FY26_Financial_Statements.pdf` | `CENT-AGRICULTURAL` | 36 |
| `Centuria_Penrose_-_FY26_Financial_Statements.pdf` | `CENT-PENROSE` | 36 |

Two things learned while collecting:

- **The Companies Register serves direct, stable PDF URLs** —
  `/companies/app/service/services/documents/<HASH>` — fetchable with `requests`, no
  session. **Disclose does not**: its links are session-scoped
  (`/disclose/document/<session-token>?nodeId=…`), so those must be clicked in a browser.
  In the in-app browser a click saves straight to `Downloads` — first as a `<uuid>.tmp`,
  renamed to the real filename a few seconds later — so wait for the named file, check it
  starts `%PDF-`, then move it into `register_reports\`. On the search page Enter does not
  submit; click the Search button.
- **Centuria files its full annual report as the company's financial statements.** The
  Penrose and Agricultural PDFs open "ANNUAL REPORT", not bare statutory accounts, so the
  earlier worry that company filings would be too thin to extract does not apply to them.
- PMG splits its filing in two: 36pp financial statements plus a 6pp Reg 62 annual report.
  Both are kept; the Reg 62 summary may carry the headline metrics the statements bury.

### Reports NOT publicly available, and whether they should be

16 of 30. The legal test: under the FMC Act 2013 a scheme offered to **retail** investors
must be registered on Disclose and file audited financial statements publicly. Offers made
only to **wholesale/eligible investors** (Schedule 1) are exempt, and overseas entities are
outside the regime entirely.

| Holding | Why not public | Should it be? |
|---|---|---|
| Centuria Industrial Fund | Not on Disclose — an NZ **company** (6806353, formerly Augusta Industrial Fund Limited), not a registered scheme | **Public** — annual financial statements on the Companies Register, FY19–FY26. FY26 extracted into `Syndicate_Periods` |
| Centuria Grenfell St | Australian unlisted: Centuria 25 Grenfell Street Fund (ARSN 656 250 154), Adelaide | Investor-only — PDS and fund updates from Centuria. FY27 forecast 2.00 cpu; fund term ends 1 Apr 2027 |
| Jasper Warrawong Plaza | NSW asset; Jasper Warrawong Plaza NZ LP invests in the Warrawong Plaza Fund, managed by **Elanor** | Investor-only — Elanor's quarterly report, forwarded via Jasper's portal. March 2026 saved to `Syndicate_Periods` |
| Centuria Govt Income 1, 2 | Australian unlisted; No. 2 is Centuria Government Income Property Fund No. 2 (ARSN 653 978 753) | Investor-only via Centuria. No. 1 not yet identified |
| E+O Heathcare, E+O/NZ Daycare, IDEAL Electrical | Erskine & Owen has no registered schemes | **Only if offered to retail.** If you invested as retail, ask why unregistered |
| Jasper Industrial Income Plus Fund | No Jasper schemes registered; the report states it is for wholesale investors only | Wholesale, so no — quarterly reports via Jasper's portal. Q1 FY27 (30 Jun 2026) saved to `Syndicate_Periods` |
| MP Innovation Carpark | No MacKersey schemes registered | As above |
| Ohanga | No My Farm schemes registered | As above |
| Surplus Brokers | No Silver Fern schemes registered | As above |
| Merx Wholeale PIE Trust 1 | Wholesale debt fund | **No** — debt/equity fund, wholesale, and excluded from property benchmarking anyway |
| PMG Preston Road | Not among PMG's 5 schemes | **No separate report likely** — a property inside a fund |
| Vickery Street Nominees (sheet: Vicky Street) | Not on Disclose under either name | From the manager — June 2026 financial statements received. **Not** Vickers Road SCH11556 |
| PMG Generation Fund | Scheme **Cancelled** (SCH12827); merged into Pacific Property Fund Limited 31 Jul 2026 | PPF's own reports are the source now — look it up on Disclose |

**The question worth putting to managers**: for any of these you hold as a *retail*
investor, a registered scheme and public audited accounts are the statutory norm. Where
that is absent, the offer was almost certainly made under the wholesale exclusion — which
is legitimate, but means you get only what the manager chooses to send, and there is no
public audited record to check it against. That is itself a governance finding.

### First real cohort: 10 syndicates extracted

11 documents, 153s, ~$10.40 all in. `Syndicate_Periods` holds **10 rows at 2026-03-31**.
9 extracted cleanly (20–28 of 32 figures each); the two PMG files came back at 14/32 and
were correctly held back as `SPARSE`.

**Two portfolio-wide patterns, visible only because everything is in one structure:**

- **Interest rate hedging has rolled off almost everywhere.** 8 of 10 syndicates have
  their earliest swap already expired or expiring within three months. That is not an
  individual syndicate problem, it is a portfolio-wide exposure to floating rates and one
  question to put to Centuria across every fund at once.
- **The ICR covenant is disclosed but the achieved ratio is not, in 7 of 10.** Covenant
  thresholds range 1.50x–2.00x. Compliance cannot be verified from any of these reports.
  Systematic, not accidental.

**Worst single syndicate: Pastoral House (Oyster)** — facility expires in 2 months
(31 Oct 2026), earliest swap expires the same month, and a **payout ratio of 138%**, so
distributions exceeded earnings. Centuria NZ Agricultural is also paying out above
earnings at 105%.

#### Cache extractions before saving

The first run saved 7 of 9 and then hit `429 Quota exceeded ... Read requests per minute`
from the Sheets API. Error isolation held — the two failures were recorded, not thrown —
but the extracted reports lived only in memory, so recovering meant paying to extract them
again.

**Extraction costs money; saving does not.** ✅ **Folded into `modules/batch.py`**
(2026-09-16), so this no longer depends on remembering it in a one-off script.

- `extract_one()` writes every report to the cache the moment it returns, before anything
  else touches it, and reads the cache before calling the API. `run_batch()` threads the
  directory through; `BatchItem.from_cache` records which documents cost nothing.
- **The key is the content hash plus the model** (`cache_key()`), not the filename, so a
  renamed file still hits and two copies of one report are extracted once — and a
  cross-check under `claude-fable-5` never reads Opus's answer.
- `load_cached()` and `store_cached()` **never raise**: a corrupt file, a full disk or a
  read-only directory is a miss, not a failed run. Writes go to a temp file and are moved
  into place, so an interrupted write cannot leave a half-file that reads as a hit.
- `save_batch()` retries a Sheets **429** with exponential backoff
  (`SAVE_MAX_ATTEMPTS`, `SAVE_BACKOFF_SECONDS`) and retries *only* rate limiting —
  a malformed row fails immediately rather than wasting a minute first. A save that
  fails anyway says so, and says the extraction is cached.
- The cache lives at `$NZWM_EXTRACT_CACHE` or a `nz_wealth_extract_cache` folder in the
  system temp directory; `cache_dir=None` disables it. `DEFAULT_CACHE_DIR` is resolved at
  call time through a sentinel, so tests (and any run that wants its own folder) redirect
  it without every caller passing it through. An autouse fixture gives each test its own
  directory — without it, one test's cached report silently satisfies the next test that
  uses the same fake bytes.
- The intake tab's cost preview subtracts what is already cached (`already_cached()`), so
  a re-run after a failure shows what it will actually cost rather than the cold-run price.

### Second cohort and holding updates, 2026-09-14

`Syndicate_Periods` gained 8 rows: the 33 Broadway and Sir William Pickering Drive FY26
annual reports from Disclose (25/32 each), and six **sparse exceptions** — Montreal/IDEAL
(13/32), MP Innovation (11), E+O Healthcare (11), NZ Daycare (10), Jasper Industrial Income
Plus (14) and Warrawong Plaza (14). These managers publish only short quarterly updates, so a
thin row is all that will ever exist and quarterly-to-quarterly comparison is like with like.
Each carries `SAVED AS SPARSE EXCEPTION` at the head of `extraction_notes`. Aliases were added
for `Erskine Owen Montreal Property Syndicate LP` and `E+O Healthcare Properties Fund LP`.

Extraction traps found in these documents — check for them before saving a sparse row:

- **The report can be wrong, not the extractor.** MP Innovation prints "WALT (years) 553
  years 514 years". Saved blank with a note rather than guessed at 5.14.
- **Amounts printed in millions come back in millions**, despite schema descriptions saying
  dollars. Warrawong returned valuation `193.0`, debt `109.5`, earnings `0.51`. Any dollar
  figure under 1,000 is a missed scale; converted ×1,000,000 before saving. Australian funds'
  dollars are AUD.
- **An average rate is not the current rate.** Sir William Pickering's stored 4.38% is the
  FY26 average; the contract rate has been 5.00% since 1 July 2025. Warrawong's 0.62 cpu is
  quarterly, not annual.

Syndicate_Data / Q3 2026 updates, one holding at a time with the user's approval: Centuria
Industrial, Surplus Brokers, PMG Direct Office, Pacific Property Fund, Vickery Street, Govt
Income 1 and 2, Grenfell St, Ohanga. Conventions this settled:

- **Current value = stated NAV/NTA per unit × units × FX.** Where no NAV per unit is stated,
  use net assets ÷ units on issue (PMG Direct Office: $0.935) and say it was derived. For a
  partnership, the partner's own capital account is the stated figure (Ohanga, $46,611).
  A newer transaction price beats an older NAV (PPF stays at the $1.1899 merger price).
- **Confirm the rate against what was actually paid.** Surplus Brokers showed 4.5% on the
  sheet; the manager's 8% was confirmed by the user's $666.67 monthly deposit.
- **Check the raw cell, not the displayed one.** `LVR_Percent` displays `0.47` while holding
  `0.4729`, so an expected-value check on the display fails. Q3's Capital Gain, Change in
  Annual $, Annual Return and Monthly Return are formulas — never write them.

Dates to watch: Govt Income 1's loan expired and a term-extension vote was held on 11 Aug
2026 (outcome unknown; its 4.5% rate awaits the July 2026 update); PPF has paid nothing since
the merger (7% is the forecast); Govt Income 2's loan expires 1 Dec 2026 with a November
vote; Grenfell St's hedge ends 15 Mar 2027, loan 31 Mar 2027 and term 1 Apr 2027 with no
strategy announced; a PPF debt tranche matures 31 May 2027.

## Benchmarking ✅ built

`modules/benchmarks.py`, **plain Python, no LLM** — same rule as `deltas`: every number
must be reproducible from stored rows. `tests/test_benchmarks.py` covers it.

`build_cohort()` takes the latest stored period per syndicate and drops what cannot be
compared: **non-property holdings** (Merx is a debt/equity fund with no cap rate) and
anything the baseline marks **sold** (Preston Road). `rank_metric()` ranks best-first per
`METRICS`, which records for each measure whether higher or lower is better.

Three deliberate properties:

- **Undisclosed is omitted, never ranked last.** Not reporting a figure is a separate
  finding that `deltas` already raises; scoring it as "worst" would double-count it and
  distort everyone else's rank. Every `Rank` carries its own `n`, which is the count of
  *disclosers*, not of the cohort.
- **Dollars are normalised against valuation** before ranking. $16,735 of capex means one
  thing on a $2m shed and another on a $115m tower.
- **Sample size travels with the answer.** With ten syndicates a bottom quartile is two or
  three of them. `sector_ranks()` refuses to rank within a sector of fewer than three.

`manager_scorecards()` is the sharpest tool here: where a manager runs several funds, their
medians say more than any one fund, and **disclosure rate is a property of the manager**,
not of the building.

### First run, FY2026, 10 syndicates

| Manager | Funds | LVR | WALE | Payout | Fees | Cap rate | Disclosure |
|---|---|---|---|---|---|---|---|
| Centuria | 8 | 42.6% | 5.2y | 86% | 0.93% | 6.25% | 71% |
| Oyster | 2 | 40.5% | 10.0y | **106%** | **2.10%** | 6.12% | 75% |

**Oyster charges 2.3x Centuria's fee load and pays out above earnings.** VIP Pacific alone
is at 3.40% of scheme property.

Trailing on the most metrics: **Augusta St Georges Bay Road** (3 of 10 — capex 0.01% of
valuation, rent reversion +28.4%, WALE 3.35y), **Centuria Penrose** (3 of 7 — highest LVR
48.7%, lowest cap rate 5.50% so the most generous valuation), and **Pastoral House**
(3 of 9 — payout 138%).

**Disclosure gaps across the portfolio** — occupancy missing from 7 of 10, interest cover
from 6, lease incentives from 6, vacancy from 5. These are not oversights in single
reports; they are what the industry has settled on not telling investors, and they are the
strongest collective question to put to managers.

Sources 2–4 (listed NZ vehicles, valuer sector data, IM promises) are still to come and
belong in one `Industry_Benchmarks` tab keyed by (metric, sector, period, source).

## The SIPO is the IM baseline

**The 5-page SIPO contains everything `Syndicate_Baseline` needs. The trust deeds do not
need to be pulled for these fields.** SIPOs run 97–320 KB against trust deeds of 2.5–10 MB
of legal drafting, and they state the scheme's own promises in plain numbered clauses:

> • Provide investors a **minimum cash return of 7% per annum** before tax on original equity
> • Property **occupancy greater than 90%**
> • Maintain the **loan to value ratio below 55%**
> • Maintain the **interest cost cover ratio not less than 2 times**
> • **NTA not less than 85%** of NTA at acquisition
> • Hedging: a **minimum 50%** of debt hedged

Thresholds differ per syndicate, so these are real per-scheme promises rather than
boilerplate:

| Syndicate | Cash return | Occupancy | LVR limit | ICR | NTA floor | Hedging |
|---|---|---|---|---|---|---|
| `SGB` | 7% | >90% | <55% | ≥2.0x | ≥85% | ≥50% |
| `CENT-AIRPARKBENDO` | 9% | >80% | <60% | ≥2.0x | ≥90% | ≥50% |
| `CENT-BUILDINGA` | 7% | >80% | <50% | — | ≥90% | — |

Collected into `Downloads\register_baseline\`. 3 of 12 so far.

### Promise versus practice, FY2026

| Syndicate | Promised | Actual | Gap |
|---|---|---|---|
| `SGB` | 7.00% | 6.75% | short 0.25 pts |
| `CENT-AIRPARKBENDO` | 9.00% | 20.00% | ahead 11 pts |
| `CENT-BUILDINGA` | 7.00% | **3.12%** | **short 3.88 pts** |

All three sit within their LVR limits, with 7.6–42.9 points of headroom.

**Check the units before believing any of these.** The 20% and 3.12% both looked wrong at
first glance. `extraction_notes` verified both: Airpark pays "18.00% to 31 July 2025, then
21.00%" on an original investment of $25,000 per unit against a 17% LVR — a long-held,
de-geared asset genuinely ahead of its promise. Building A's rate "changed during the year
from 4.25%" and its payout ratio is 39%, so the earnings exist and are being retained. The
question for Building A is why, when the SIPO promises 7%.

**The hedging clause is the sharpest finding available.** SIPOs commit to a minimum 50% of
debt hedged, and the FY2026 cohort shows 8 of 10 syndicates with their earliest swap
already expired or expiring within three months. That is a stated policy testable directly
against disclosed fact, per syndicate, by name.

### Baseline extraction needs its own schema

`SyndicateReport` is built for periodic reports; a SIPO or trust deed run through it would
return near-zero completeness and trip `SPARSE_THRESHOLD`. A `BaselinePolicy` schema —
cash return, occupancy floor, LVR ceiling, ICR floor, NTA floor, hedging minimum, fee
entitlements, distribution-suspension triggers — is a small, well-shaped job. SIPOs are
short and highly consistent in structure, so extraction should be cheap and accurate.

## BaselinePolicy: the SIPO schema ✅ built

`modules/policy.py` extracts a SIPO into the IM baseline. Separate from `SyndicateReport`
on purpose — a SIPO run through the report schema returns near-zero completeness and trips
`SPARSE_THRESHOLD`, because they answer different questions. Same discipline: grouped,
every field required and nullable, page and verbatim quote on every value.

`to_baseline_row()` maps onto `Syndicate_Baseline` columns and **only writes what the SIPO
states**, so a silent document never blanks a value entered by hand.

All 12 SIPOs extracted in 95s (~$4). Written to the baseline.

| Syndicate | Return | Occupancy | LVR | ICR | NTA | Hedging |
|---|---|---|---|---|---|---|
| `CENT-WILLIAMSSTRE` | **10%** | >90% | <40% | 2.0x | 90% | ≥50% |
| `CENT-AIRPARKBENDO` | 9% | >80% | <60% | 2.0x | 90% | ≥50% |
| `CENT-CARTERSBIRCH` | 8% | >90% | <45% | 2.0x | 90% | ≥50% |
| `SGB` / `CENT-AIRWAYSSOE` | 7% | >90% | <55% | 2.0x | 85–90% | ≥50% |
| `CENT-BROADWAY33` | 7% | >75% | <55% | 2.0x | 85% | — |
| `CENT-BUILDINGA` / `B` | 7% | >80% | <50% | 2.0x | 90% | — |
| `OYST-PASTORALHOUS` | 6% | — | <55% | — | — | — |
| `PMG-OFFICE` | — | >80% | <50% | — | — | — |

**Oyster and PMG commit to far less than Centuria.** Centuria's SIPOs state a return, an
occupancy floor, an LVR ceiling, an ICR floor and an NTA floor. Oyster states a return and
an LVR ceiling; PMG states no return at all. Less promised is less to be held to.

### Promise versus FY2026 practice

| Syndicate | Promised | Actual | Gap |
|---|---|---|---|
| `CENT-AIRPARKBENDO` | 9% | 20% | **+11.00** |
| `CENT-WILLIAMSSTRE` | 10% | 12% | +2.00 |
| `SGB` | 7% | 6.75% | −0.25 |
| `OYST-PASTORALHOUS` | 6% | 2.64% | **−3.36** |
| `CENT-BUILDINGA` | 7% | 3.12% | **−3.88** |
| `CENT-BUILDINGB` | 7% | 2.00% | **−5.00** |

Every syndicate is inside its LVR ceiling. **Both Graham Street buildings pay less than
half what their SIPOs promise**, on payout ratios of 39% — the earnings exist and are being
retained.

**Four SIPOs commit to hedging a minimum 50% of debt, and all four have their earliest swap
already expired**: Williams Street 2025-04-05, Airpark 2026-04-07, SGB 2026-06-05. That is
a written policy against a disclosed fact, per syndicate, by name.

### Watch the expiry on a promise

SGB's SIPO qualifies its 7% as applying "until 31 March 2020". A regex over the text missed
that; the schema caught it in `cash_return_basis`. **Check the basis before treating any
gap as a shortfall** — several of these promises are dated, and the SIPOs themselves are
from 2019–2025.

### The promises are columns now — and two of the three cannot be tested

`to_baseline_row()` wrote the cash return, LVR ceiling and ICR floor into real columns but
left the occupancy floor, NTA floor and hedging minimum inside the `trust_deed_notes`
prose, where no rule could reach them. `Syndicate_Baseline` now has
**`occupancy_floor_percent`, `nta_floor_percent` and `hedging_minimum_percent`**, backfilled
for the 12 SIPO syndicates by parsing the notes string that `to_baseline_row()` itself wrote
— free, and no SIPO needed re-extracting. Three rules in `deltas.py` test them:
`hedging_below_policy` / `hedging_policy_unverifiable`, `occupancy_below_policy`,
`nta_below_policy`.

**`total_swap_notional` could never have tested a hedging policy.** It sums every swap a
report lists, expired ones included, which is how Williams Street read as **179% hedged**
against its own debt. `flatten_report()` now also derives **`live_swap_notional` and
`live_swap_count`** — the swaps still running at the period end — via `_live_hedging()`,
which returns three distinguishable things: a number, a real `0.0` when every listed swap
has expired (the sharpest test there is), and `None` for unknown, including when a live swap
states no notional, because a partial sum would understate the hedge and manufacture a
breach. The 27 stored rows were backfilled from `raw_json`.

**Even the live notional exceeds the debt.** Augusta 125%, Williams Street 116%, Airpark
104%, Pickering Drive 107% — these swaps are staggered or forward-starting, one beginning as
another ends, and the schema records expiry dates but no start dates. So the rule reports
`hedging_policy_unverifiable` rather than reading "complies" off a sum that cannot mean what
it appears to. **All four syndicates that promise a 50% hedging minimum come back
unverifiable**, which is itself the finding: the commitment cannot be checked from the
reports the manager publishes.

**The occupancy floor is worse: the two sets are disjoint.** Nine syndicates promise an
occupancy floor (Centuria's eight plus PMG Direct Office); twelve disclose occupancy (the
Australian funds, E+O, Jasper, Oyster, MacKersey, Industrial, Diversified). **The overlap is
zero.** Every scheme that committed to a floor is one that does not publish the number, and
every manager who publishes it promised nothing. `occupancy_below_policy` therefore fires on
nothing today — not a bug, and the strongest single question to put to Centuria and PMG.

`nta_below_policy` is **dormant by design**: the floor is a percentage of NTA *at
acquisition*, `formation_nav_per_unit` is empty for all 30, and
`original_investment_per_unit` is not the same figure — issue costs mean a $50,000 unit
starts below $50,000 of NTA, so substituting it would manufacture breaches. Filling
`formation_nav_per_unit` from each IM or first annual report switches eight syndicates on.

## Industry_Benchmarks ✅ built

`modules/industry.py` plus an `Industry_Benchmarks` tab. Both external sources — listed NZ
vehicles and valuer sector series — land in one tab keyed by
**(metric, sector, period_end, source)**, so the module reads one mechanism rather than two.

| Column | Purpose |
|---|---|
| `metric` | must be a key from `benchmarks.METRICS`, so comparison is mechanical |
| `sector` | must match the syndicate's sector, or be `All` |
| `region`, `period_end`, `value`, `unit` | the figure itself |
| `source`, `source_type` | `listed_vehicle` / `valuer` / `index` / `other` |
| `basis_notes` | **how it is measured** — the comparability test |
| `url`, `entered_at` | audit trail |

**Every row must come from a citable source.** The tab started deliberately empty, and that
rule still governs what goes in it: a benchmark gets used in an argument with a manager, and
that is exactly when an unsourced figure becomes worthless. It now holds **29 usable rows**
from four listed vehicles (see *Goodman and Precinct added* below).

Two of the three rules are refusals, and `parse_benchmarks()` drops any row that breaks
them rather than using it:

- **No cross-sector comparison.** An Office cap rate says nothing about a childcare centre.
  A sector-specific benchmark beats an `All` one where both exist.
- **No comparison without a stated basis.** A listed REIT's gearing is measured on total
  assets; a single-asset syndicate's LVR is not the same number. `basis_notes` is required
  and travels into every `Comparison` so the reader sees how the figure was built.
- **No hindsight.** A benchmark dated after the period being judged is not applied.

`coverage()` answers "how well can we benchmark this?" *before* any conclusion is drawn
from a thin reference set.

### What to collect, and for which sectors

The cohort is now **27 syndicates**: Office 10, Industrial 9, Agriculture 2, Diversified 2,
and one each of Car Parking, Childcare, Healthcare and Retail. Only Office and Industrial
clear `MIN_SECTOR_COHORT`, so only those two get sector-relative ranks. Measurable across the
cohort, so worth finding a benchmark for:

| Metric | Measurable | Best external source |
|---|---|---|
| `lvr_percent` | 26 of 27 | listed vehicles |
| `wale_years` | 24 | listed vehicles |
| `cash` | 21 | listed vehicles |
| `capitalisation_rate_percent` | 16 | valuer sector series (CBRE/Colliers/JLL) |
| `payout_ratio_percent` | 14 | listed vehicles |
| `occupancy_percent`, `icr_actual`, `capex_spent` | 12 each | listed vehicles |
| `manager_fee_total_percent` | 11 | listed vehicles' management expense ratios |
| `rent_reversion_percent` | 8 | valuer market rent series |
| `vacancy_percent` | 8 | valuer sector vacancy |
| `lease_incentives_paid` | 6 | listed vehicles |

Listed comparators: Precinct, Argosy, Goodman, Kiwi Property, Property for Industry. All
publish semi-annually, and **all file on the NZX** rather than the Disclose Register.

Benchmarks cannot fix a disclosure gap. Lease incentives are missing from 21 of 27 reports,
vacancy and rent reversion from 19, fees from 16, interest cover and occupancy from 15 —
those stay questions for the managers, not gaps an external series can fill.

### Listed vehicles collected: Argosy and PFI

11 benchmark rows, from the two vehicles whose results are most directly comparable.
Presentations are in `Downloads\listed_vehicles\`; both were fetched by direct URL and read
locally with `pypdf`, so collecting these cost nothing.

| Source | Balance date | Sector | Metrics |
|---|---|---|---|
| **Argosy Property** FY26 | **31 Mar 2026** — same as the syndicates | Diversified (55% ind / 35% off / 10% LFR) | occupancy 94.6%, WALT 5.0y, cap rate 6.26%, payout 97%, gearing 37.2%, under-rented 9.3% |
| **Property for Industry** FY26 | 30 Jun 2026 — *three months later* | Pure Industrial | occupancy 98.7%, WALT 5.04y, LVR 34.2%, payout 87%, under-rented 7.1% |

**Three basis differences are recorded on the rows and must not be forgotten:**

- **Argosy's 37.2% is debt to TOTAL ASSETS, not LVR on property value.** A syndicate's LVR
  is debt over the valuation, so the listed figure understates the comparable number. The
  LVR gaps below are indicative, not exact.
- **Payout is to AFFO** for both listed vehicles; syndicates report distributions to
  distributable profit. Related, different denominator.
- **PFI's balance date is 30 June**, a quarter after the syndicates'.

### What the comparison says

**Rent reversion is the standout, and it is not close.** Both listed vehicles are
*under*-rented — Argosy by 9.3%, PFI by 7.1% — meaning their rents rise as leases roll.
**Four of six syndicates are over-rented**: Augusta +28.4%, Building B +23.5%, Williams
Street +18.2%, Building A +12.1%. On the same measure the gap to Argosy is 21 to 38 points.
Only Penrose and Airpark sit on the right side of market.

**Gearing runs higher across the portfolio.** Seven of ten syndicates are above Argosy's
37.2%, led by Penrose at 48.7%. Allowing for the total-assets basis, the real gap is wider
still.

**WALE splits the portfolio in two.** Five syndicates beat the 5.0-year benchmark
comfortably — Agricultural 15.5y, Penrose 14.9y, VIP Pacific 11.5y — while four trail it,
Williams Street worst at 1.42 years against 5.0.

**Cap rates cluster tighter than the benchmark.** Airpark and Penrose at 5.50% against
Argosy's 6.26% means a more generous valuation on those two.

**Payout looks good until you read the basis.** Eight of ten sit below Argosy's 97%, but
Argosy's is to AFFO. Pastoral House at 138% and Agricultural at 105% are above it on any
reading.

Coverage is now 10/10 for LVR, payout and WALE, 9/9 for cap rate, 6/6 for rent reversion
and 3/3 for occupancy. Still nothing for fees, capex, cash, vacancy or interest cover —
Precinct, Goodman and Kiwi Property would add the office and retail comparators, and a
valuer series would add sector cap rates and vacancy.

### Goodman and Precinct added, sector labels fixed

18 rows added on 2026-09-14, taking the tab to 29. Goodman and Precinct were read from their
own annual reports and presentations, fetched by URL and parsed locally.

| Source | Balance date | Sector | What it gives |
|---|---|---|---|
| **Goodman NZ** annual report | **31 Mar 2026** — same as most syndicates | Industrial | occupancy 96.9%, WALT 4.9y, cap rate 5.9%, LVR 19.8%, payout 85.5%, under-rented 19.5% |
| **Precinct** interim presentation | 31 Dec 2025 | Office | occupancy 97% by NLA, WALT 6.1y, cap rate 5.8%, gearing 37.2%, under-rented 6% |
| **Precinct** annual report | 30 Jun 2026 | Office | occupancy 97%, WALT 7.1y, cap rate 5.9%, gearing 36.3%, payout 104.8% to AFFO, under-rented 3%, **ICR 2.1x** |

Precinct's ICR is the first interest-cover benchmark in the tab. Two figures were deliberately
left out: its half-year payout ratio (106% over six months against 90–92% full-year guidance)
and its 49bp management expense ratio, which covers running the listed company and is not
comparable with a syndicate's fee on scheme property.

**Goodman is an outlier on gearing, and the no-hindsight rule hands it the March industrial
rows.** Its 19.8% is look-through and follows nearly $700m of asset sales — a year earlier it
was 31.8%. Because it is sector-specific and not dated after the March periods, `applicable()`
prefers it over PFI's 34.2%, which makes every industrial syndicate look 20–29 points worse
than a fairer comparator would. Adding **Argosy's and PFI's half-year figures at 31 Dec 2025**
is the fix, and would also give the March office rows an ICR benchmark.

**Three sector labels were wrong, and sector ranks depend on them:**

| Syndicate | Was | Now | Evidence |
|---|---|---|---|
| `CENT-BROADWAY33` | Commercial Property | **Office** | Mercury's head office; office and retail over five levels |
| `CENT-WILLIAMSSTRE` | Commercial Property | **Industrial** | FY26 report p21: "a large cold store facility with office" |
| `MYFA-OHANGA` | Commercial | **Agriculture** | accounts list poultry sheds, broiler units, pastoral land |

Fixed in `Syndicate_Baseline` **and** the matching `Syndicate_Data.Sector` cells, so the
dashboards and the cohort agree. `MACK-MPINNOVATION` stays **Car Parking** — its report
describes 14 car-park decks plus five ground-floor retail units.

What the re-run changed: office syndicates are now measured against Precinct rather than
Argosy's all-sector figures, which is a sharper test — 33 Broadway is **+31% over market rent
against Precinct's −6%**, and Augusta, Building A and Building B all trail Precinct's 6.1-year
WALT by more than two years. Cap rates above Precinct's read as conservative, but part of that
gap is simply secondary stock against premium CBD towers. Still zero benchmarks for capex,
cash, fees, vacancy and lease incentives.

## Cross-checking extractions with a second model

`scripts/crosscheck_fable.py` re-extracts the stored PDFs with a different model and diffs
against `raw_json` in `Syndicate_Periods`. **It never overwrites stored data** — it reports,
you decide.

```bash
python scripts/crosscheck_fable.py                    # preview and cost estimate
python scripts/crosscheck_fable.py --run              # run the whole set
python scripts/crosscheck_fable.py --run --only SGB   # one syndicate
```

Where two models independently agree on a figure, it can be put to a manager with
confidence. Where they disagree, that figure needs a human eye before it becomes evidence.

Two habits are built in. Each extraction is **written to disk before comparing**, so a
crash never forces a paid re-run — this matters more on a second-model pass funded by
finite credits. And figures are compared with a 1% tolerance, because 46.52 and 46.5 are
the same number at different precision and flagging that would bury the real disagreements.

### Pointing extraction at a different model

`DEFAULT_MODEL` in `modules/extraction.py` stays `claude-opus-5`. **Pass the model
explicitly rather than changing the default** — Fable is $10/$50 per MTok against Opus's
$5/$25, so a default switch silently doubles the cost of every future quarter once the
credits are gone.

```python
extract_report(pdf_bytes=..., api_key=key, model="claude-fable-5")
batch.run_batch(documents, baseline, api_key=key, model="claude-fable-5")
```

Fable specifics that differ from Opus: thinking is always on, so `{"type": "disabled"}`
returns a 400 (the code passes `adaptive`, which is fine); `temperature` and `top_p` are
rejected (not used); and it needs **30-day data retention**, which this account has —
confirmed by a live call returning `claude-fable-5` cleanly.
