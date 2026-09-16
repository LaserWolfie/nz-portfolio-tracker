"""When a decision is actually available, and what repricing debt would do to it.

**Plain Python, no LLM**, same rule as `deltas` and `benchmarks`: every number here must
be reproducible from stored rows, and nothing in this module recommends a course of
action. It reports dates and arithmetic; the decision is the reader's.

Two things it answers.

**When can you act?** A syndicate unit is not a listed share. For most of these holdings
there is no continuous market, so a decision exists only at particular moments: the fund
term expiring (usually with an investor vote), a bank facility maturing, a hedge rolling
off, or a sale resolution already passed. `windows()` collects those dates from stored
figures and returns them in order, so the next decision point is a fact rather than a
surprise in the post.

**What does repricing do?** Hedging has rolled off across this portfolio, with swaps
struck years ago expiring into a materially higher market. `refinance_sensitivity()`
models what a rate shock costs in dollars, what share of adjusted operating profit that
consumes, and whether the distribution is still covered afterwards.

Three refusals, for the same reason they exist elsewhere in this codebase.

**Blank is never zero.** An undisclosed debt balance or a missing operating profit makes
a scenario incomputable, and the scenario says so. It does not quietly model zero debt.

**A hedge that cannot be measured is not a hedge of zero, nor of everything.** Where the
live swap notional exceeds the debt -- which is every syndicate that discloses one,
because the swaps are staggered -- the unhedged portion cannot be derived, so only the
dated worst case (all debt exposed once the earliest swap expires) is reported.

**No advice.** Nothing here scores a holding as buy, sell or hold. The output is a date,
a dollar figure and a coverage ratio.
"""

from dataclasses import dataclass
from datetime import date

from modules.deltas import _date, _months_between, _num, _text

#: Rate shock applied by default, in percentage points. Swaps rolling off in this
#: portfolio were struck between 1.34% and 3.76%; a 2 point rise is a plausible
#: repricing rather than a stress test, and every function takes the points as an
#: argument so a different assumption can be run without editing this file.
DEFAULT_SHOCK_POINTS = 2.0

#: Distribution cover below this is "not covered": the syndicate would be paying
#: distributions out of something other than the period's earnings.
COVER_FLOOR = 1.0

#: How many months of trading a report covers, inferred from its own `report_type`.
#: A quarterly report's earnings compared against a year of extra interest overstates
#: the damage fourfold, which is exactly what Warrawong Plaza did before this existed.
PERIOD_MONTHS = (
    (("quarter",), 3),
    (("biannual", "half", "interim"), 6),
    (("annual", "financial statement", "year"), 12),
)


def period_months(report_type) -> int | None:
    """Months of trading the report covers, or None when its own label does not say."""
    text = (_text(report_type) or "").lower()
    if not text:
        return None
    for markers, months in PERIOD_MONTHS:
        if any(marker in text for marker in markers):
            return months
    return None


#: Decision kinds, most consequential first. A fund term expiring is the moment the
#: holding itself is decided; a facility or hedge expiring changes the economics but
#: rarely offers an exit.
TERM_EXPIRY = "fund_term_expiry"
FACILITY_EXPIRY = "facility_expiry"
SWAP_EXPIRY = "swap_expiry"

KIND_ORDER = {TERM_EXPIRY: 0, FACILITY_EXPIRY: 1, SWAP_EXPIRY: 2}


@dataclass(frozen=True)
class DecisionWindow:
    """One dated moment at which something can or must be decided."""

    syndicate_id: str
    name: str
    kind: str
    when: date
    months_away: float
    detail: str
    source_field: str

    @property
    def is_past(self) -> bool:
        return self.months_away < 0

    def __str__(self):
        when = f"{self.when:%d %b %Y}"
        timing = "PASSED" if self.is_past else f"{self.months_away:.0f} months"
        return f"{when} ({timing}) {self.syndicate_id}: {self.detail}"


def windows(period_rows: list[dict], baseline_rows: list[dict] | None = None,
            as_at: date | None = None, horizon_months: float | None = None) -> list[DecisionWindow]:
    """Every dated decision point across the portfolio, soonest first.

    Reads the latest stored period per syndicate. `fund_term_expiry` comes from the
    baseline because a scheme's term is a constitutional fact, not a reporting-period
    one; the facility and swap dates come from the period row.

    `horizon_months` drops anything further away than that. Past dates are always kept:
    a facility that matured last month is a live problem, not history.
    """
    as_at = as_at or date.today()
    baseline = {str(b.get("syndicate_id")): b for b in (baseline_rows or [])
                if b.get("syndicate_id")}

    latest: dict[str, dict] = {}
    for row in period_rows:
        syndicate_id = str(row.get("syndicate_id") or "")
        period_end = str(row.get("period_end") or "")
        if not syndicate_id or not period_end:
            continue
        if syndicate_id not in latest or period_end > str(latest[syndicate_id].get("period_end") or ""):
            latest[syndicate_id] = row

    found = []
    for syndicate_id, row in latest.items():
        base = baseline.get(syndicate_id, {})
        name = _text(base.get("canonical_name")) or syndicate_id

        for kind, value, field, describe in (
            (TERM_EXPIRY, base.get("fund_term_expiry"), "fund_term_expiry",
             "fund term expires -- investors are normally asked to extend, wind up or sell"),
            (FACILITY_EXPIRY, row.get("effective_facility_expiry"), "effective_facility_expiry",
             "bank facility expires -- refinancing terms reset here"),
            (SWAP_EXPIRY, row.get("earliest_swap_expiry"), "earliest_swap_expiry",
             "earliest interest rate swap expires -- debt starts repricing"),
        ):
            when = _date(value)
            if when is None:
                continue
            months = _months_between(as_at, when)
            if horizon_months is not None and months > horizon_months:
                continue
            found.append(DecisionWindow(
                syndicate_id=syndicate_id,
                name=name,
                kind=kind,
                when=when,
                months_away=months,
                detail=describe,
                source_field=field,
            ))

    found.sort(key=lambda w: (w.when, KIND_ORDER.get(w.kind, 9)))
    return found


@dataclass(frozen=True)
class RefinanceScenario:
    """What a rate shock costs one syndicate, and whether the distribution survives it."""

    syndicate_id: str
    name: str
    shock_points: float
    debt: float | None
    exposed_debt: float | None
    exposure_basis: str
    extra_interest: float | None
    operating_profit: float | None
    profit_consumed_percent: float | None
    distributions: float | None
    cover_before: float | None
    cover_after: float | None
    note: str = ""

    @property
    def is_computable(self) -> bool:
        return self.extra_interest is not None

    @property
    def distribution_uncovered_after(self) -> bool | None:
        if self.cover_after is None:
            return None
        return self.cover_after < COVER_FLOOR


def refinance_sensitivity(row: dict, baseline: dict | None = None,
                          shock_points: float = DEFAULT_SHOCK_POINTS) -> RefinanceScenario:
    """Model a rate rise on one stored period row.

    The exposed debt is the whole facility, not the unhedged slice. Where a report
    discloses swap notionals at all they exceed the debt, because the swaps are
    staggered and the schema records no start dates -- so the share hedged at any one
    moment cannot be derived, and pretending otherwise would understate the exposure.
    The dated worst case is the honest one: once the earliest swap expires, the whole
    facility is repricing unless the manager replaces the hedge.

    Distributions are taken from the payout ratio applied to adjusted operating profit,
    because that is the pair the reports actually disclose together.

    Profit is annualised from the report's own period before it meets a year of extra
    interest: a quarterly report's earnings against twelve months of interest overstates
    the damage fourfold. Where the report does not say what period it covers, cover is
    left unknown rather than assumed annual.
    """
    syndicate_id = str(row.get("syndicate_id") or "")
    name = _text((baseline or {}).get("canonical_name")) or syndicate_id

    debt = _num(row.get("total_debt"))
    reported_profit = _num(row.get("adjusted_operating_profit"))
    payout = _num(row.get("payout_ratio_percent"))
    live_notional = _num(row.get("live_swap_notional"))

    notes = []
    months = period_months(row.get("report_type"))
    profit = reported_profit
    if reported_profit is not None:
        if months is None:
            profit = None
            notes.append(
                f"report_type {row.get('report_type')!r} does not say what period it covers, "
                "so the profit cannot be annualised and cover is left unknown"
            )
        elif months != 12:
            profit = reported_profit * 12 / months
            notes.append(
                f"profit annualised from a {months}-month report "
                f"({reported_profit:,.0f} -> {profit:,.0f})"
            )
    if live_notional is not None and debt and live_notional > debt:
        notes.append(
            f"live swap notional ({live_notional:,.0f}) exceeds debt ({debt:,.0f}), so the "
            "swaps are staggered and the share hedged at any one time is not derivable"
        )

    if debt is None:
        return RefinanceScenario(
            syndicate_id, name, shock_points, None, None, "debt not disclosed",
            None, profit, None, None, None, None,
            "; ".join(notes + ["total debt is not disclosed, so no scenario can be run"]),
        )

    extra_interest = debt * shock_points / 100
    distributions = (profit * payout / 100) if (profit is not None and payout is not None) else None
    profit_consumed = (extra_interest / profit * 100) if profit else None

    cover_before = (profit / distributions) if (profit is not None and distributions) else None
    cover_after = ((profit - extra_interest) / distributions) if (profit is not None and distributions) else None

    if profit is None:
        notes.append("adjusted operating profit is not disclosed, so cover cannot be computed")
    elif payout is None:
        notes.append("payout ratio is not disclosed, so distributions cannot be estimated")

    return RefinanceScenario(
        syndicate_id=syndicate_id,
        name=name,
        shock_points=shock_points,
        debt=debt,
        exposed_debt=debt,
        exposure_basis="whole facility once the earliest swap expires",
        extra_interest=extra_interest,
        operating_profit=profit,
        profit_consumed_percent=profit_consumed,
        distributions=distributions,
        cover_before=cover_before,
        cover_after=cover_after,
        note="; ".join(notes),
    )


def sensitivity_all(period_rows: list[dict], baseline_rows: list[dict] | None = None,
                    shock_points: float = DEFAULT_SHOCK_POINTS) -> list[RefinanceScenario]:
    """Run the scenario over the latest period of every syndicate, worst cover first."""
    baseline = {str(b.get("syndicate_id")): b for b in (baseline_rows or [])
                if b.get("syndicate_id")}

    latest: dict[str, dict] = {}
    for row in period_rows:
        syndicate_id = str(row.get("syndicate_id") or "")
        period_end = str(row.get("period_end") or "")
        if not syndicate_id or not period_end:
            continue
        if syndicate_id not in latest or period_end > str(latest[syndicate_id].get("period_end") or ""):
            latest[syndicate_id] = row

    scenarios = [refinance_sensitivity(row, baseline.get(sid), shock_points)
                 for sid, row in latest.items()]
    # Worst cover first; incomputable ones last, since they are a disclosure finding
    # rather than a ranked result.
    scenarios.sort(key=lambda s: (s.cover_after is None, s.cover_after if s.cover_after is not None else 0))
    return scenarios


@dataclass(frozen=True)
class IncomeAtRisk:
    """The family's own income sitting behind syndicates whose cover fails the shock."""

    shock_points: float
    total_income: float
    income_uncovered: float
    income_unknown: float
    holdings_uncovered: list[tuple[str, str, float]]   # (syndicate_id, owner, annual distribution)

    @property
    def uncovered_share(self) -> float:
        return (self.income_uncovered / self.total_income * 100) if self.total_income else 0.0


def income_at_risk(period_rows: list[dict], baseline_rows: list[dict],
                   holding_rows: list[dict], resolve, shock_points: float = DEFAULT_SHOCK_POINTS,
                   income_column: str = "Annual_Distribution",
                   entity_column: str = "Entity_Name",
                   owner_column: str = "Owner_Entity") -> IncomeAtRisk:
    """How much of the family's annual distribution income sits behind an uncovered cover ratio.

    `resolve` is passed in rather than imported so this module never depends on the
    storage layer: pass `storage.resolve_syndicate_id`.

    A holding whose syndicate has no stored period, or whose cover cannot be computed,
    is counted as unknown -- never as safe.
    """
    scenarios = {s.syndicate_id: s for s in sensitivity_all(period_rows, baseline_rows, shock_points)}

    total = uncovered = unknown = 0.0
    flagged = []
    for holding in holding_rows:
        income = _num(holding.get(income_column))
        if income is None or income == 0:
            continue
        total += income
        syndicate_id = resolve(str(holding.get(entity_column, "")), baseline_rows)
        scenario = scenarios.get(syndicate_id) if syndicate_id else None
        if scenario is None or scenario.cover_after is None:
            unknown += income
            continue
        if scenario.distribution_uncovered_after:
            uncovered += income
            flagged.append((syndicate_id, str(holding.get(owner_column, "")).strip(), income))

    flagged.sort(key=lambda item: -item[2])
    return IncomeAtRisk(shock_points, total, uncovered, unknown, flagged)
