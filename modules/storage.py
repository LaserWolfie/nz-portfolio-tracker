"""Per-period storage for extracted syndicate reports.

Two new tabs, both written **by header name**. The existing `Syndicate_Data`
tab is left untouched so the current dashboards keep working.

- `Syndicate_Periods`  -- one row per syndicate per period end.
- `Syndicate_Baseline` -- one row per syndicate: identity, aliases, and the
  IM-derived reference figures the delta engine compares against.

Why header-name writes: the old `save_to_google_sheet` built a 22-element list
and appended it positionally, so inserting a column anywhere in the sheet
silently shifted every value into the wrong field. Here a row is a dict keyed by
header text; if a header is missing we raise rather than guess.

The period columns are derived from `SyndicateReport` itself, so adding a field
to the schema extends the sheet rather than silently dropping the new data.
"""

from datetime import datetime, timezone

from modules import sheets
from modules.extraction import completeness
from modules.schema import SyndicateReport, figure_names, iter_figures

PERIODS_WORKSHEET = "Syndicate_Periods"
BASELINE_WORKSHEET = "Syndicate_Baseline"

ALIAS_SEPARATOR = "|"

#: Columns that identify and date the row, before any extracted figure.
PERIOD_KEY_COLUMNS = [
    "syndicate_id",
    "period_end",
    "entity_name_as_reported",
    "source_filename",
    "extracted_at",
    "model",
]

#: Derived columns the delta engine reads directly rather than reparsing JSON.
PERIOD_DERIVED_COLUMNS = [
    "effective_facility_expiry",
    "earliest_swap_expiry",
    "total_swap_notional",
    "swap_count",
    "manager_fee_total_dollars",
    "manager_fee_total_percent",
    "rent_reversion_percent",
    "completeness_found",
    "completeness_total",
    "raw_json",
]

BASELINE_COLUMNS = [
    "syndicate_id",
    "canonical_name",
    "aliases",
    "owner_entity",
    "manager_name",
    "sector",
    "im_date",
    "im_forecast_distribution_rate",
    "im_forecast_distribution_unit",
    "formation_nav_per_unit",
    "original_investment_per_unit",
    "icr_covenant_threshold",
    "lvr_covenant_threshold",
    "trust_deed_notes",
    "notes",
]


def _figure_field_names() -> list[str]:
    """Schema fields that wrap a single value with provenance, in schema order.

    Delegates to the schema so regrouping fields never silently drops a column.
    """
    return figure_names()


def period_columns() -> list[str]:
    """The full `Syndicate_Periods` header row, derived from the schema."""
    columns = list(PERIOD_KEY_COLUMNS)
    for name in _figure_field_names():
        columns.append(name)
        columns.append(f"{name}_page")
    columns.append("distribution_unit")
    columns.append("extraction_notes")
    columns.extend(PERIOD_DERIVED_COLUMNS)
    return columns


class StorageError(Exception):
    """Raised when the sheet's shape does not match what we are trying to write."""


# --------------------------------------------------------------------------
# Header-name writing
# --------------------------------------------------------------------------

def read_header(worksheet) -> list[str]:
    header = worksheet.row_values(1)
    return [str(h).strip() for h in header]


def row_from_dict(header: list[str], values: dict) -> list:
    """Order `values` to match `header`, raising on any key the sheet lacks.

    Unknown keys are an error, not a silent drop: if the schema has grown a
    field and the sheet has not, we want to hear about it before writing a row
    that is missing data.
    """
    missing = [k for k in values if k not in header]
    if missing:
        raise StorageError(
            f"The sheet has no column for: {', '.join(sorted(missing))}. "
            "Run ensure_worksheets() to add the missing headers."
        )
    return [_cell(values.get(column)) for column in header]


def _cell(value):
    """gspread wants JSON-safe scalars; None becomes an empty cell, not 0."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return value


# --------------------------------------------------------------------------
# Flattening an extracted report
# --------------------------------------------------------------------------

def _earliest(dates: list[str | None]) -> str | None:
    real = sorted(d for d in dates if d)
    return real[0] if real else None


def flatten_report(
    report: SyndicateReport,
    syndicate_id: str,
    source_filename: str = "",
    model: str = "",
) -> dict:
    """Turn a SyndicateReport into one flat row keyed by column name.

    Scalar figures become `<field>` and `<field>_page`. The repeating groups
    (swaps, fees) are summarised into the few numbers the delta engine needs;
    the complete structure is preserved verbatim in `raw_json` so nothing is
    lost and any figure can be traced back.
    """
    row = {
        "syndicate_id": syndicate_id,
        "period_end": report.identity.period_end_date.value,
        "entity_name_as_reported": report.identity.entity_name.value,
        "source_filename": source_filename,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
    }

    for name, figure in iter_figures(report):
        row[name] = figure.value
        row[f"{name}_page"] = figure.page

    row["distribution_unit"] = report.returns.distribution_unit.value
    row["extraction_notes"] = report.extraction_notes

    # A facility that matures next month but was refinanced after balance date
    # is not a live risk. Prefer the post-balance-date expiry where disclosed.
    row["effective_facility_expiry"] = (
        report.debt.post_balance_date_facility_expiry.value
        or report.debt.facility_expiry.value
    )

    swaps = report.debt.swap_expiries
    row["earliest_swap_expiry"] = _earliest([s.expiry_date for s in swaps])
    notionals = [s.notional_amount for s in swaps if s.notional_amount]
    row["total_swap_notional"] = sum(notionals) if notionals else None
    row["swap_count"] = len(swaps)

    fees = report.conduct.manager_fees
    dollars = [f.amount for f in fees if f.amount]
    percents = [f.percent_of_scheme_property for f in fees if f.percent_of_scheme_property]
    row["manager_fee_total_dollars"] = sum(dollars) if dollars else None
    row["manager_fee_total_percent"] = sum(percents) if percents else None

    # How far passing rent sits above (positive) or below (negative) market rent.
    # Positive means rents fall as leases roll; negative means reversionary upside.
    market = report.tenancy.net_market_rent_per_sqm.value
    passing = report.tenancy.net_passing_rent_per_sqm.value
    row["rent_reversion_percent"] = (
        round((passing - market) / market * 100, 2) if market and passing else None
    )

    found, total = completeness(report)
    row["completeness_found"] = found
    row["completeness_total"] = total

    row["raw_json"] = report.model_dump_json()
    return row


# --------------------------------------------------------------------------
# Syndicate identity
# --------------------------------------------------------------------------

#: Legal forms that vary freely between documents for the same entity.
LEGAL_SUFFIXES = {"limited", "ltd", "the", "lp", "partnership", "l.p"}


def _normalise(name: str) -> str:
    """Loose comparison key: case, punctuation and legal suffixes vary by document."""
    if not name:
        return ""
    text = str(name).lower()
    for noise in (",", ".", "(", ")", "'", "’", "-", "  "):
        text = text.replace(noise, " ")
    words = [w for w in text.split() if w not in LEGAL_SUFFIXES]
    return " ".join(words)


def load_baseline(worksheet) -> list[dict]:
    """Every baseline row as a dict keyed by header name."""
    records = worksheet.get_all_records()
    return [dict(r) for r in records]


def resolve_syndicate_id(entity_name: str, baseline_rows: list[dict]) -> str | None:
    """Match a reported entity name to a syndicate_id via canonical name or alias.

    Returns None when there is no confident match -- the caller should ask
    rather than guess, because filing a report against the wrong syndicate
    corrupts two histories at once.
    """
    target = _normalise(entity_name)
    if not target:
        return None

    for row in baseline_rows:
        candidates = [row.get("canonical_name", "")]
        aliases = str(row.get("aliases", "") or "")
        candidates.extend(aliases.split(ALIAS_SEPARATOR))
        for candidate in candidates:
            if candidate and _normalise(candidate) == target:
                return str(row.get("syndicate_id")) or None

    # Fall back to containment, which catches "Augusta St Georges Bay Road
    # Property Trust" against a baseline holding "St Georges Bay Road".
    matches = set()
    for row in baseline_rows:
        canonical = _normalise(row.get("canonical_name", ""))
        if canonical and (canonical in target or target in canonical):
            matches.add(str(row.get("syndicate_id")))
    return matches.pop() if len(matches) == 1 else None


# --------------------------------------------------------------------------
# Worksheet provisioning and writing
# --------------------------------------------------------------------------

def ensure_worksheets(spreadsheet=None) -> dict:
    """Create the two tabs if absent, and add any headers the schema has grown.

    Only ever adds columns. Never reorders or removes them, so a sheet someone
    has added their own working columns to stays usable.
    """
    spreadsheet = spreadsheet or sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    existing = {ws.title for ws in spreadsheet.worksheets()}
    result = {}

    for title, columns in (
        (PERIODS_WORKSHEET, period_columns()),
        (BASELINE_WORKSHEET, BASELINE_COLUMNS),
    ):
        if title not in existing:
            worksheet = spreadsheet.add_worksheet(
                title=title, rows=200, cols=max(len(columns) + 5, 26)
            )
            worksheet.update(range_name="A1", values=[columns])
            result[title] = f"created with {len(columns)} columns"
            continue

        worksheet = spreadsheet.worksheet(title)
        header = read_header(worksheet)
        added = [c for c in columns if c not in header]
        if added:
            new_header = header + added
            if worksheet.col_count < len(new_header):
                worksheet.add_cols(len(new_header) - worksheet.col_count)
            worksheet.update(range_name="A1", values=[new_header])
            result[title] = f"added {len(added)} column(s): {', '.join(added)}"
        else:
            result[title] = "up to date"

    return result


def upsert_period(worksheet, row: dict) -> str:
    """Write one period row, replacing any existing row for the same period.

    Re-scanning a document must not create a second, conflicting history for
    the same quarter, so the (syndicate_id, period_end) pair is the key.
    """
    syndicate_id = row.get("syndicate_id")
    period_end = row.get("period_end")
    if not syndicate_id or not period_end:
        raise StorageError("A period row needs both syndicate_id and period_end.")

    header = read_header(worksheet)
    ordered = row_from_dict(header, row)

    id_col = header.index("syndicate_id") + 1
    period_col = header.index("period_end") + 1
    ids = worksheet.col_values(id_col)
    periods = worksheet.col_values(period_col)

    for line in range(2, max(len(ids), len(periods)) + 1):
        existing_id = ids[line - 1] if line - 1 < len(ids) else ""
        existing_period = periods[line - 1] if line - 1 < len(periods) else ""
        if existing_id == str(syndicate_id) and existing_period == str(period_end):
            worksheet.update(
                range_name=f"A{line}",
                values=[ordered],
            )
            return f"updated row {line}"

    worksheet.append_row(ordered, value_input_option="USER_ENTERED")
    return "appended"


def save_report(
    report: SyndicateReport,
    syndicate_id: str,
    source_filename: str = "",
    model: str = "",
    spreadsheet=None,
) -> str:
    """Flatten and persist one extracted report."""
    spreadsheet = spreadsheet or sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    worksheet = spreadsheet.worksheet(PERIODS_WORKSHEET)
    row = flatten_report(report, syndicate_id, source_filename, model)
    return upsert_period(worksheet, row)
