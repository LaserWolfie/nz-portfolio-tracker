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
    """One line of manager remuneration, as categorised by the report itself.

    Fees are commonly disclosed as a percentage of scheme property rather than as
    a dollar amount, and sometimes as both. Record whichever the report gives and
    null the other -- never convert between them.
    """

    category: str = Field(
        description="Fee category exactly as the report labels it, e.g. "
        "'Scheme management fees', 'Property management fees', 'Supervisor fees'. "
        "Do not invent categories or merge lines the report presents separately."
    )
    amount: float | None = Field(
        description="Dollar amount for the period, if the report states one. "
        "Null if the fee is only given as a percentage."
    )
    percent_of_scheme_property: float | None = Field(
        description="The fee as a whole percent of scheme property, e.g. 0.42 for 0.42%, "
        "if the report states one. Null if the fee is only given in dollars."
    )
    page: int | None = Field(description="1-based PDF page number.")


class Identity(BaseModel):
    """Who and when. Extracted first so the model anchors on the document."""

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


class Valuation(BaseModel):
    """What the property is worth and the assumptions behind that number."""

    valuation: Figure = Field(
        description="Most recent stated property valuation, in dollars."
    )
    valuation_date: DateFigure = Field(
        description="Effective date of that valuation, which is often earlier than "
        "the period end date."
    )
    nta_per_unit: Figure = Field(
        description="Net tangible assets per unit, in dollars per unit. Commonly labelled "
        "'Net assets per unit' or 'NAV per unit'. Take the precise figure from the financial "
        "statements or key information summary, not a rounded headline like '$45K'."
    )
    cash: Figure = Field(description="Cash and cash equivalents held, in dollars.")
    capitalisation_rate_percent: Figure = Field(
        description="The adopted market capitalisation rate used in the valuation, as a "
        "whole percent, e.g. 6.38 for 6.38%. Where a sensitivity table shows rates either "
        "side, take the ADOPTED rate, not the +/- scenarios."
    )
    discount_rate_percent: Figure = Field(
        description="The discount rate used in the discounted cash flow valuation, as a "
        "whole percent, e.g. 7.50."
    )
    terminal_yield_percent: Figure = Field(
        description="The terminal yield used in the valuation, as a whole percent, "
        "e.g. 6.50."
    )


class Debt(BaseModel):
    """Borrowings, covenants and hedging."""

    total_debt: Figure = Field(description="Total drawn bank debt, in dollars.")
    facility_expiry: DateFigure = Field(
        description="Expiry / maturity date of the bank facility. If several facilities "
        "exist, use the earliest expiry."
    )
    post_balance_date_facility_expiry: DateFigure = Field(
        description="If the report discloses in its subsequent-events note that the "
        "facility was refinanced, extended or replaced AFTER the balance date, the expiry "
        "date of the new facility. Null if no such event is disclosed. This matters "
        "because a facility expiring weeks after balance date may already have been "
        "refinanced for years by the time the report is published."
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
        "This is the minimum the syndicate must maintain, not the achieved figure. "
        "Beware: the word 'covenant' also appears in supervisor company names such as "
        "'Covenant Trustee Services Limited', which has nothing to do with banking "
        "covenants. Many reports disclose no ICR covenant at all -- record null."
    )
    swap_expiries: list[SwapExpiry] = Field(
        description="Every interest rate swap or hedge tranche disclosed. Empty list if "
        "the report discloses no hedging. Dates are usually printed day-first "
        "(8/06/2026 is 8 June 2026, not 6 August)."
    )


class Tenancy(BaseModel):
    """Income security: who is paying, for how long, and at what rent."""

    occupancy_percent: Figure = Field(
        description="Occupancy as a whole percent, e.g. 97.5 for 97.5%. Record this ONLY "
        "if the report explicitly labels a figure as occupancy. Never derive it from a "
        "vacancy figure -- put that in vacancy_percent instead."
    )
    vacancy_percent: Figure = Field(
        description="Vacancy as a whole percent, e.g. 0.24 for 0.24%, where the report "
        "states vacancy rather than occupancy. Many NZ reports give one or the other, "
        "not both."
    )
    wale_years: Figure = Field(
        description="Weighted average lease expiry in years, e.g. 4.2. Also reported as "
        "WALT. Never expressed as a percentage."
    )
    net_market_rent_per_sqm: Figure = Field(
        description="Assessed net MARKET rent per square metre, in dollars. This is what "
        "the valuer considers the space would let for today."
    )
    net_passing_rent_per_sqm: Figure = Field(
        description="Net PASSING rent per square metre, in dollars -- what tenants are "
        "actually paying under current leases. Distinct from market rent: a property let "
        "above market will see rent fall as leases roll."
    )
    net_lettable_area_sqm: Figure = Field(
        description="Net lettable area of the property in square metres."
    )
    nbs_rating_percent: Figure = Field(
        description="Seismic rating as a percentage of New Building Standard (NBS), e.g. "
        "100 for 100% NBS. Null if the report does not state one."
    )


class Returns(BaseModel):
    """What reached investors, and what supported it."""

    distribution_rate: Figure = Field(
        description="The distribution rate for this period as printed. Reports often "
        "carry TWO different rates -- an 'average distribution rate' expressed on the "
        "original investment, and a 'distribution yield' expressed on closing equity. "
        "They are both correct and they differ. Take the 'average distribution rate' on "
        "original investment where both appear, and say so in extraction_notes."
    )
    distribution_unit: DistributionUnit = Field(
        description="Which convention the distribution rate above uses. Use 'unknown' "
        "if the report does not make the basis explicit; do not guess."
    )
    payout_ratio_percent: Figure = Field(
        description="Distributions as a whole percent of distributable profit, "
        "e.g. 95.0 for 95%."
    )
    adjusted_operating_profit: Figure = Field(
        description="The report's headline ADJUSTED earnings figure for the period, in "
        "dollars: the figure a non-GAAP reconciliation arrives at after removing fair "
        "value movements and other non-cash items from net profit. Managers label it "
        "differently and every one of these IS this field: 'adjusted operating profit', "
        "'adjusted net profit', 'distributable profit', 'adjusted funds from operations'. "
        "Record the reconciliation's result under whatever name it carries. Only the "
        "unadjusted GAAP subtotals -- the plain 'Operating profit' and 'Net profit' lines "
        "that the reconciliation starts from -- are excluded. Record null only when the "
        "report contains no such reconciliation at all."
    )
    adjusted_operating_profit_forecast: Figure = Field(
        description="The forecast or budgeted AOP for the same period, in dollars, where "
        "the report states one to compare against."
    )


class Conduct(BaseModel):
    """What the manager charged and how it behaved."""

    manager_fees: list[ManagerFee] = Field(
        description="Every manager remuneration line disclosed, split by the report's own "
        "categories. Empty list if none is disclosed."
    )
    management_fee_escalation_basis: TextFigure = Field(
        description="How the management fee changes over time, quoted or closely "
        "paraphrased from the report -- for example an annual increase at the greater of "
        "3% or CPI. This is the clause that determines whether fees rise independently of "
        "performance. Null if the report does not describe one."
    )
    capex_spent: Figure = Field(
        description="Capital expenditure on the property during the period, in dollars, "
        "including capitalised building works and investment property additions. Evidence "
        "of whether the manager is reinvesting in the asset."
    )
    lease_incentives_paid: Figure = Field(
        description="Lease incentives paid or capitalised during the period, in dollars. "
        "Large incentives can mean headline rents are being sustained by giving value "
        "back to tenants."
    )
    related_party_transactions: TextFigure = Field(
        description="What the report says about related party transactions -- quote or "
        "closely paraphrase. Many reports state there were none; record that statement "
        "rather than null, so silence and an explicit 'none' stay distinguishable."
    )


class SyndicateReport(BaseModel):
    """One investor report for one syndicate for one period.

    Grouped rather than flat for a hard reason: the structured-output grammar is
    compiled from the JSON Schema, and its size grows with the number of
    TOP-LEVEL required properties. At 36 flat fields the API rejects the request
    with "The compiled grammar is too large". The same 34 leaf figures nested
    under six groups compile without complaint. Add new fields inside a group,
    never at the top level.
    """

    identity: Identity
    valuation: Valuation
    debt: Debt
    tenancy: Tenancy
    returns: Returns
    conduct: Conduct

    extraction_notes: str | None = Field(
        description="Anything ambiguous, contradictory, or restated that a human should "
        "check: figures given on two different bases, prior-period restatements, unusual "
        "one-offs. Null if nothing of the sort."
    )


class FinancialPass(BaseModel):
    """First extraction pass: the money and the debt."""

    identity: Identity
    valuation: Valuation
    debt: Debt
    returns: Returns
    extraction_notes: str | None = Field(
        description="Anything ambiguous, contradictory or restated in these figures that "
        "a human should check. Null if nothing of the sort."
    )


class AssetPass(BaseModel):
    """Second extraction pass: the building, its tenants, and the manager's conduct."""

    tenancy: Tenancy
    conduct: Conduct
    extraction_notes: str | None = Field(
        description="Anything ambiguous, contradictory or restated in these figures that "
        "a human should check. Null if nothing of the sort."
    )


def merge_passes(financial: FinancialPass, asset: AssetPass) -> "SyndicateReport":
    """Combine the two passes into the single report the rest of the app uses."""
    notes = [n for n in (financial.extraction_notes, asset.extraction_notes) if n]
    return SyndicateReport(
        identity=financial.identity,
        valuation=financial.valuation,
        debt=financial.debt,
        tenancy=asset.tenancy,
        returns=financial.returns,
        conduct=asset.conduct,
        extraction_notes=" ".join(notes) or None,
    )


#: Group attribute names on SyndicateReport, in extraction order.
GROUP_NAMES = ["identity", "valuation", "debt", "tenancy", "returns", "conduct"]


def iter_figures(report):
    """Yield (leaf_name, figure) for every provenance-wrapped value in the report.

    Lets storage and completeness walk the schema without caring how it is
    grouped, so regrouping never silently drops a column.
    """
    for group_name in GROUP_NAMES:
        group = getattr(report, group_name)
        for name in group.__class__.model_fields:
            value = getattr(group, name)
            if isinstance(value, (Figure, DateFigure, TextFigure)):
                yield name, value


def figure_names() -> list[str]:
    """Leaf figure names in schema order, without needing an instance."""
    names = []
    for group_name, group_field in SyndicateReport.model_fields.items():
        if group_name not in GROUP_NAMES:
            continue
        for name, field in group_field.annotation.model_fields.items():
            if field.annotation in (Figure, DateFigure, TextFigure):
                names.append(name)
    return names


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

7. The manager's letter and the key information summary routinely round: a letter saying
   "an LVR of 47%" and a summary tile reading "46.52%" are the same metric. Always record
   the more precise figure, and prefer the key information summary and the financial
   statements over prose. Note the discrepancy only if the two genuinely disagree rather
   than merely round.

8. Figures are not always labelled. Facility maturity in particular is often stated only
   in narrative commentary - for example "reflecting its maturity on 30 September 2026"
   inside a paragraph about reclassifying the loan to current liabilities. Read the
   commentary, not just the tables.

9. The key information summary is usually a grid of visual tiles rather than a table.
   Match each number to the tile it sits in, using the tile's own caption and footnote
   marker. Do not pair a number with a caption just because they are adjacent in reading
   order.
"""


#: What each pass is looking for, so the model knows where to concentrate.
PASS_FOCUS = {
    "financial": (
        "Concentrate on the financial statements, the key information summary and the "
        "borrowings note: valuation, net assets, cash, debt, facility and covenant terms, "
        "hedging, distributions and earnings."
    ),
    "asset": (
        "Concentrate on the property and valuation notes and the fees disclosures: "
        "occupancy or vacancy, lease expiry, market and passing rents, lettable area, "
        "seismic rating, capital expenditure, lease incentives, manager fees and related "
        "party dealings."
    ),
}


def build_extraction_prompt(syndicate_name: str | None = None, focus: str | None = None) -> str:
    """The per-document user instruction accompanying the PDF."""
    target = (
        f"This report is expected to relate to {syndicate_name}. If the document is "
        "plainly for a different syndicate, still extract what the document says and note "
        "the mismatch in extraction_notes - do not correct the document to match the "
        "expected name.\n\n"
        if syndicate_name
        else ""
    )
    focus_text = f"{PASS_FOCUS[focus]}\n\n" if focus in PASS_FOCUS else ""
    return (
        f"{target}{focus_text}Extract this investor report into the schema. Work through "
        "the document and record each field with its page number and a verbatim supporting "
        "quote, using null for anything the document does not state."
    )
