"""Fixed extraction schema for syndicate investor reports.

Design rules, all of which the delta engine depends on:

1. **Every field is required, and nullable.** No field carries a Python default,
   so Pydantic marks it `required` in the generated JSON Schema while still
   permitting `null`. The model must therefore emit every key on every run, and
   `null` becomes an explicit statement of "not present in this document" rather
   than an omission. A field that silently disappears and a field the manager
   stopped reporting must be distinguishable.

2. **Every figure carries provenance.** `page` and `source_text` let any number
   be traced back to the document. Native PDF citations cannot be used here --
   the API rejects `citations` combined with `output_config.format` -- so
   provenance is modelled as ordinary schema fields instead.

3. **Units are pinned in the field descriptions and never inferred.** Percentages
   are whole numbers (45.0 means 45%), ratios are multiples (2.5 means 2.5x),
   money is in the report's own currency units.

   NOTE: never pass ICR, WALE, or payout ratio through `utils.clean_percent` --
   it divides by 100 whenever a value exceeds 2.0, which turns an ICR of 2.5x
   into 0.025. See CLAUDE.md.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Figure(BaseModel):
    """A single extracted number and where it came from."""

    value: float | None = Field(
        description="The figure exactly as printed, with no scaling, rounding, "
        "annualising or other derivation. Null if the document does not state it."
    )
    page: int | None = Field(
        description="1-based PDF page number the figure appears on. Null if value is null."
    )
    source_text: str | None = Field(
        description="Short verbatim quote from the document containing the figure, "
        "copied character for character. Null if value is null."
    )


class DateFigure(BaseModel):
    """A single extracted date and where it came from."""

    value: str | None = Field(
        description="Date in ISO YYYY-MM-DD form. If the document gives only a month "
        "and year, use the last day of that month. Null if the document does not state it."
    )
    page: int | None = Field(description="1-based PDF page number. Null if value is null.")
    source_text: str | None = Field(
        description="Short verbatim quote containing the date. Null if value is null."
    )


class TextFigure(BaseModel):
    """A single extracted string and where it came from."""

    value: str | None = Field(description="The text as printed. Null if not stated.")
    page: int | None = Field(description="1-based PDF page number. Null if value is null.")
    source_text: str | None = Field(
        description="Short verbatim quote. Null if value is null."
    )


class DistributionUnit(str, Enum):
    """How the distribution rate is expressed. NZ syndicates use both conventions."""

    CENTS_PER_UNIT = "cents_per_unit_per_annum"
    PERCENT_OF_SUBSCRIPTION = "percent_per_annum_on_subscription_price"
    PERCENT_OF_CURRENT_VALUE = "percent_per_annum_on_current_value"
    UNKNOWN = "unknown"


class SwapExpiry(BaseModel):
    """One interest rate swap or hedge tranche."""

    expiry_date: str | None = Field(description="ISO YYYY-MM-DD. Null if not stated.")
    notional_amount: float | None = Field(
        description="Hedged notional in dollars. Null if not stated."
    )
    fixed_rate_percent: float | None = Field(
        description="Fixed rate as a whole percent, e.g. 4.85 for 4.85%. Null if not stated."
    )
    page: int | None = Field(description="1-based PDF page number.")


class ManagerFee(BaseModel):
    """One line of manager remuneration, as categorised by the report itself."""

    category: str = Field(
        description="Fee category exactly as the report labels it, e.g. "
        "'Management fee', 'Leasing fee', 'Accounting fee'. Do not invent categories "
        "or merge lines that the report presents separately."
    )
    amount: float | None = Field(description="Dollar amount for the period. Null if not stated.")
    page: int | None = Field(description="1-based PDF page number.")


class SyndicateReport(BaseModel):
    """One investor report for one syndicate for one period.

    Field order here is the extraction order. Identity and period first so the
    model anchors on the document before reading figures out of it.
    """

    # --- Identity and period -------------------------------------------------
    entity_name: TextFigure = Field(
        description="Full legal name of the syndicate / scheme as printed on this report."
    )
    manager_name: TextFigure = Field(description="The manager or issuer named on the report.")
    period_end_date: DateFigure = Field(
        description="The balance date this report covers, NOT the date it was issued or "
        "the date it was signed."
    )
    report_type: TextFigure = Field(
        description="How the report describes itself, e.g. 'Quarterly Update', "
        "'Annual Report', 'Interim Financial Statements'."
    )

    # --- Valuation and capital ----------------------------------------------
    valuation: Figure = Field(
        description="Most recent stated property valuation, in dollars."
    )
    valuation_date: DateFigure = Field(
        description="Effective date of that valuation, which is often earlier than "
        "the period end date."
    )
    nta_per_unit: Figure = Field(
        description="Net tangible assets per unit, in dollars per unit."
    )
    cash: Figure = Field(description="Cash and cash equivalents held, in dollars.")

    # --- Debt ----------------------------------------------------------------
    total_debt: Figure = Field(description="Total drawn bank debt, in dollars.")
    facility_expiry: DateFigure = Field(
        description="Expiry / maturity date of the bank facility. If several facilities "
        "exist, use the earliest expiry."
    )
    lvr_percent: Figure = Field(
        description="Loan to value ratio as a whole percent, e.g. 42.5 for 42.5%."
    )
    icr_actual: Figure = Field(
        description="Interest coverage ratio actually achieved, as a multiple, "
        "e.g. 2.5 for 2.5x or 250%. Convert a percentage to a multiple only when the "
        "report itself labels it as a percentage."
    )
    icr_covenant: Figure = Field(
        description="The bank covenant threshold for interest coverage, as a multiple. "
        "This is the minimum the syndicate must maintain, not the achieved figure."
    )
    swap_expiries: list[SwapExpiry] = Field(
        description="Every interest rate swap or hedge tranche disclosed. Empty list if "
        "the report discloses no hedging."
    )

    # --- Property performance -------------------------------------------------
    occupancy_percent: Figure = Field(
        description="Occupancy as a whole percent, e.g. 97.5 for 97.5%. If the report "
        "states vacancy instead, record vacancy here only if it is explicitly labelled "
        "occupancy; otherwise null."
    )
    wale_years: Figure = Field(
        description="Weighted average lease expiry in years, e.g. 4.2. Also reported as "
        "WALT. Never expressed as a percentage."
    )

    # --- Distributions ---------------------------------------------------------
    distribution_rate: Figure = Field(
        description="The distribution rate for this period as printed."
    )
    distribution_unit: DistributionUnit = Field(
        description="Which convention the distribution rate above uses. Use 'unknown' "
        "if the report does not make the basis explicit; do not guess."
    )
    payout_ratio_percent: Figure = Field(
        description="Distributions as a whole percent of distributable profit, "
        "e.g. 95.0 for 95%."
    )

    # --- Earnings ----------------------------------------------------------------
    adjusted_operating_profit: Figure = Field(
        description="Adjusted operating profit (AOP), or the report's nearest equivalent "
        "such as distributable profit, for the period, in dollars."
    )
    adjusted_operating_profit_forecast: Figure = Field(
        description="The forecast or budgeted AOP for the same period, in dollars, where "
        "the report states one to compare against."
    )

    # --- Fees ----------------------------------------------------------------------
    manager_fees: list[ManagerFee] = Field(
        description="Every manager remuneration line disclosed, split by the report's own "
        "categories. Empty list if none is disclosed."
    )

    # --- Extraction notes -------------------------------------------------------------
    extraction_notes: str | None = Field(
        description="Anything ambiguous, contradictory, or restated that a human should "
        "check: figures given on two different bases, prior-period restatements, unusual "
        "one-offs. Null if nothing of the sort."
    )


EXTRACTION_SYSTEM_PROMPT = """\
You extract figures from New Zealand property syndicate investor reports into a fixed schema.

Your output is used to compute covenant headroom and period-on-period changes for an
investor. A wrong number is far worse than a missing one: a missing number is visible and
gets chased, a wrong number silently produces a false reading of the investment.

Rules:

1. Record only what the document states. Never infer, estimate, annualise, pro-rate,
   average, or compute a figure from other figures - even when the arithmetic is obvious.
   If the report gives quarterly interest cover and you want an annual figure, do not
   derive it. Record the quarterly figure or record null.

2. When a figure is not in the document, set its `value` to null, and set `page` and
   `source_text` to null as well. Never omit a key. Never substitute zero for absent -
   zero is a real reported value and means something different from "not disclosed".

3. For every figure you do record, give the 1-based page number and a short verbatim
   quote copied character for character from the document. If you cannot point to the
   text, you have not found the figure - record null.

4. Take the figure for THIS reporting period. These reports routinely print prior-period
   comparatives beside current figures, and forecast columns beside actuals. Read the
   column headings before taking a number.

5. Respect the units named in each field description. Percentages are whole numbers
   (45.0 means 45%). Ratios are multiples (2.5 means 2.5x). Do not rescale a figure to
   make it look conventional - if a report states an interest coverage ratio of 250%,
   that is 2.5 as a multiple, but if it states 2.5, that is also 2.5. Never divide a
   ratio by 100 on your own initiative.

6. If the same metric appears more than once on different bases (for example a
   consolidated and a look-through LVR), take the one the report presents as the headline
   figure and describe the ambiguity in `extraction_notes`.
"""


def build_extraction_prompt(syndicate_name: str | None = None) -> str:
    """The per-document user instruction accompanying the PDF."""
    target = (
        f"This report is expected to relate to {syndicate_name}. If the document is "
        "plainly for a different syndicate, still extract what the document says and note "
        "the mismatch in extraction_notes - do not correct the document to match the "
        "expected name.\n\n"
        if syndicate_name
        else ""
    )
    return (
        f"{target}Extract this investor report into the schema. Work through the document "
        "and record each field with its page number and a verbatim supporting quote, using "
        "null for anything the document does not state."
    )
