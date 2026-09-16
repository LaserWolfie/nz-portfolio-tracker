"""Delta engine: rank syndicates by what changed and what is about to break.

**Plain Python, no LLM.** Every flag here must be reproducible and explainable
from two stored rows and a baseline. Nothing in this module calls an API.

The input rows are `Syndicate_Periods` records as `get_all_records()` returns
them, so values arrive as strings, numbers, or `''` for an empty cell.

Two rules this module exists to honour:

1. **Blank is not zero.** A Google Sheets cell for an undisclosed figure comes
   back as `''`. Treating that as 0.0 would report a distribution cut to nil, an
   LVR of zero, and a covenant breach, all from a manager who simply did not
   publish the number. `_num()` returns None for blanks and never defaults to 0.
   For the same reason this module does not use `utils.clean_number` (defaults
   to 0.0) or `utils.clean_percent` (divides by 100 above 2.0, which destroys
   ICR and WALE -- see CLAUDE.md).

2. **Compare like with like.** A distribution rate quoted on subscription price
   is not comparable with one quoted on closing equity. Where the basis changed
   between periods the engine says so instead of computing a fictional delta.
"""

from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime
from enum import IntEnum

# --- Thresholds ------------------------------------------------------------
# Tunable in one place rather than scattered through the rules.

EXPIRY_WARNING_MONTHS = 18
EXPIRY_URGENT_MONTHS = 6

ICR_BREACH_RATIO = 1.00       # actual / covenant at or below this is a breach
ICR_THIN_RATIO = 1.15
ICR_WATCH_RATIO = 1.30

LVR_COVENANT_NEAR_POINTS = 5.0   # percentage points below the covenant

MATERIAL_DISTRIBUTION_CUT = 10.0  # percent relative decline
MATERIAL_OCCUPANCY_DROP = 5.0     # percentage points
MATERIAL_VALUATION_DROP = 10.0    # percent relative decline

PAYOUT_RATIO_UNSUSTAINABLE = 100.0

#: A property syndicate's valuation below this is not a small property, it is a table
#: printed in thousands and read as dollars. Centuria NZ Diversified stored a "valuation"
#: of 153,980 and a "loan balance" of 60,475 against a fund of $154m and $60m.
IMPLAUSIBLE_VALUATION = 1_000_000.0

# Live swap notional above this share of debt cannot be a point-in-time hedge: the
# swaps must be staggered or forward-starting.
HEDGE_SHARE_IMPLAUSIBLE = 100.0


class Severity(IntEnum):
    """Ordered so flags sort naturally, most serious first."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    def __str__(self):
        return self.name

    def __format__(self, spec):
        # IntEnum would otherwise inherit int.__format__, so f"{severity:<8}"
        # renders "3" instead of "HIGH" and quietly leaks into the UI.
        return format(self.name, spec)


@dataclass(frozen=True)
class Flag:
    """One ranked observation about one syndicate for one period."""

    code: str
    severity: Severity
    field: str
    message: str
    current: object = None
    prior: object = None
    evidence_page: object = None

    def __str__(self):
        return f"[{self.severity}] {self.message}"


# --------------------------------------------------------------------------
# Safe parsing
# --------------------------------------------------------------------------

def _num(value) -> float | None:
    """Parse a sheet cell to a number, or None. Never defaults to zero.

    A blank cell means "not disclosed" and must stay distinguishable from a
    genuine reported 0.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if text == "" or text.lower() in {"none", "nan", "n/a", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value) -> date | None:
    """Parse an ISO date cell, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _months_between(start: date, end: date) -> float:
    return (end.year - start.year) * 12 + (end.month - start.month) + (end.day - start.day) / 30.44


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
# Each rule takes (current, prior, baseline, as_at) and returns a list of Flags.
# Prior and baseline may be None; a rule must cope.

def _rule_facility_expiry(current, prior, baseline, as_at) -> list[Flag]:
    """Facility maturity inside the warning window.

    Uses `effective_facility_expiry`, which prefers a post-balance-date
    refinance where the report disclosed one. Flagging a facility that has
    already been refinanced for three years is the fastest way to make the
    whole report untrustworthy.
    """
    expiry = _date(current.get("effective_facility_expiry")) or _date(
        current.get("facility_expiry")
    )
    if expiry is None:
        return []

    months = _months_between(as_at, expiry)
    refinanced = _date(current.get("post_balance_date_facility_expiry")) is not None
    note = " (post-balance-date refinance)" if refinanced else ""

    if months < 0:
        return [Flag("facility_expired", Severity.CRITICAL, "effective_facility_expiry",
                     f"Bank facility expired on {expiry:%d %b %Y}{note}", expiry)]
    if months <= EXPIRY_URGENT_MONTHS:
        return [Flag("facility_expiry_urgent", Severity.CRITICAL, "effective_facility_expiry",
                     f"Bank facility expires in {months:.0f} months "
                     f"({expiry:%d %b %Y}){note}", expiry)]
    if months <= EXPIRY_WARNING_MONTHS:
        return [Flag("facility_expiry_near", Severity.HIGH, "effective_facility_expiry",
                     f"Bank facility expires in {months:.0f} months "
                     f"({expiry:%d %b %Y}){note}", expiry)]
    return []


def _rule_swap_expiry(current, prior, baseline, as_at) -> list[Flag]:
    """Hedging rolling off leaves the syndicate exposed to floating rates."""
    expiry = _date(current.get("earliest_swap_expiry"))
    if expiry is None:
        return []
    months = _months_between(as_at, expiry)
    if months < 0:
        return [Flag("swap_expired", Severity.HIGH, "earliest_swap_expiry",
                     f"Earliest interest rate swap expired {expiry:%d %b %Y}; "
                     "check what remains hedged", expiry)]
    if months <= EXPIRY_WARNING_MONTHS:
        severity = Severity.HIGH if months <= EXPIRY_URGENT_MONTHS else Severity.MEDIUM
        return [Flag("swap_expiry_near", severity, "earliest_swap_expiry",
                     f"Earliest interest rate swap expires in {months:.0f} months "
                     f"({expiry:%d %b %Y})", expiry)]
    return []


def _rule_icr(current, prior, baseline, as_at) -> list[Flag]:
    """Interest coverage against its covenant, and the headroom trend."""
    actual = _num(current.get("icr_actual"))
    covenant = _num(current.get("icr_covenant"))
    if covenant is None and baseline:
        covenant = _num(baseline.get("icr_covenant_threshold"))

    flags = []

    # A covenant you cannot test is its own problem: the report tells you the
    # threshold but not whether it was met.
    if covenant is not None and actual is None:
        flags.append(Flag(
            "icr_actual_not_disclosed", Severity.MEDIUM, "icr_actual",
            f"ICR covenant of {covenant:.2f}x is disclosed but the achieved ratio is not, "
            "so compliance cannot be verified from this report",
            None, None, current.get("icr_covenant_page")))

    if actual is not None and covenant is not None and covenant > 0:
        ratio = actual / covenant
        if ratio <= ICR_BREACH_RATIO:
            flags.append(Flag("icr_breach", Severity.CRITICAL, "icr_actual",
                              f"ICR {actual:.2f}x is at or below the {covenant:.2f}x covenant",
                              actual))
        elif ratio <= ICR_THIN_RATIO:
            flags.append(Flag("icr_headroom_thin", Severity.HIGH, "icr_actual",
                              f"ICR {actual:.2f}x leaves little headroom over the "
                              f"{covenant:.2f}x covenant", actual))
        elif ratio <= ICR_WATCH_RATIO:
            flags.append(Flag("icr_headroom_watch", Severity.MEDIUM, "icr_actual",
                              f"ICR {actual:.2f}x is within sight of the {covenant:.2f}x "
                              "covenant", actual))

    if prior and actual is not None:
        prior_actual = _num(prior.get("icr_actual"))
        if prior_actual is not None and actual < prior_actual:
            flags.append(Flag("icr_declining", Severity.MEDIUM, "icr_actual",
                              f"ICR fell from {prior_actual:.2f}x to {actual:.2f}x",
                              actual, prior_actual))
    return flags


def _rule_lvr(current, prior, baseline, as_at) -> list[Flag]:
    """Leverage against the covenant, and the trend."""
    lvr = _num(current.get("lvr_percent"))
    if lvr is None:
        return []

    flags = []
    covenant = _num(baseline.get("lvr_covenant_threshold")) if baseline else None
    if covenant is not None:
        if lvr >= covenant:
            flags.append(Flag("lvr_breach", Severity.CRITICAL, "lvr_percent",
                              f"LVR {lvr:.2f}% is at or above the {covenant:.2f}% covenant",
                              lvr))
        elif lvr >= covenant - LVR_COVENANT_NEAR_POINTS:
            flags.append(Flag("lvr_near_covenant", Severity.HIGH, "lvr_percent",
                              f"LVR {lvr:.2f}% is within {covenant - lvr:.2f} points of the "
                              f"{covenant:.2f}% covenant", lvr))

    if prior:
        prior_lvr = _num(prior.get("lvr_percent"))
        if prior_lvr is not None and lvr > prior_lvr:
            flags.append(Flag("lvr_rising", Severity.LOW, "lvr_percent",
                              f"LVR rose from {prior_lvr:.2f}% to {lvr:.2f}%", lvr, prior_lvr))
    return flags


def _rule_distribution(current, prior, baseline, as_at) -> list[Flag]:
    """Distribution cut against the prior period, and against the IM forecast.

    Only compares rates quoted on the same basis. A rate on subscription price
    and one on closing equity are different measures; differencing them would
    invent a cut or hide one.
    """
    rate = _num(current.get("distribution_rate"))
    unit = _text(current.get("distribution_unit"))
    flags = []

    if prior:
        prior_rate = _num(prior.get("distribution_rate"))
        prior_unit = _text(prior.get("distribution_unit"))
        if rate is not None and prior_rate is not None:
            if unit and prior_unit and unit != prior_unit:
                flags.append(Flag(
                    "distribution_basis_changed", Severity.MEDIUM, "distribution_unit",
                    f"Distribution basis changed from {prior_unit} to {unit}; "
                    "the rates are not directly comparable", unit, prior_unit))
            elif rate < prior_rate and prior_rate > 0:
                decline = (prior_rate - rate) / prior_rate * 100
                severity = (
                    Severity.HIGH if decline >= MATERIAL_DISTRIBUTION_CUT else Severity.MEDIUM
                )
                flags.append(Flag("distribution_cut", severity, "distribution_rate",
                                  f"Distribution rate cut {decline:.1f}%, from {prior_rate:g} "
                                  f"to {rate:g}", rate, prior_rate))

    if baseline and rate is not None:
        forecast = _num(baseline.get("im_forecast_distribution_rate"))
        forecast_unit = _text(baseline.get("im_forecast_distribution_unit"))
        comparable = not (unit and forecast_unit) or unit == forecast_unit
        if forecast is not None and comparable and forecast > 0 and rate < forecast:
            shortfall = (forecast - rate) / forecast * 100
            flags.append(Flag("below_im_forecast", Severity.MEDIUM, "distribution_rate",
                              f"Distribution rate {rate:g} is {shortfall:.1f}% below the IM "
                              f"forecast of {forecast:g}", rate, forecast))

    payout = _num(current.get("payout_ratio_percent"))
    if payout is not None and payout > PAYOUT_RATIO_UNSUSTAINABLE:
        flags.append(Flag("payout_above_earnings", Severity.HIGH, "payout_ratio_percent",
                          f"Payout ratio of {payout:.0f}% means distributions exceeded "
                          "earnings for the period", payout))
    return flags


def _rule_tenancy(current, prior, baseline, as_at) -> list[Flag]:
    """Occupancy falling or vacancy rising, whichever the manager reports."""
    flags = []

    occupancy = _num(current.get("occupancy_percent"))
    if prior and occupancy is not None:
        prior_occupancy = _num(prior.get("occupancy_percent"))
        if prior_occupancy is not None and occupancy < prior_occupancy:
            drop = prior_occupancy - occupancy
            severity = Severity.HIGH if drop >= MATERIAL_OCCUPANCY_DROP else Severity.MEDIUM
            flags.append(Flag("occupancy_drop", severity, "occupancy_percent",
                              f"Occupancy fell {drop:.2f} points, from {prior_occupancy:.2f}% "
                              f"to {occupancy:.2f}%", occupancy, prior_occupancy))

    vacancy = _num(current.get("vacancy_percent"))
    if prior and vacancy is not None:
        prior_vacancy = _num(prior.get("vacancy_percent"))
        if prior_vacancy is not None and vacancy > prior_vacancy:
            rise = vacancy - prior_vacancy
            severity = Severity.HIGH if rise >= MATERIAL_OCCUPANCY_DROP else Severity.MEDIUM
            flags.append(Flag("vacancy_rise", severity, "vacancy_percent",
                              f"Vacancy rose {rise:.2f} points, from {prior_vacancy:.2f}% "
                              f"to {vacancy:.2f}%", vacancy, prior_vacancy))

    wale = _num(current.get("wale_years"))
    if prior and wale is not None:
        prior_wale = _num(prior.get("wale_years"))
        if prior_wale is not None and wale < prior_wale:
            flags.append(Flag("wale_shortening", Severity.LOW, "wale_years",
                              f"WALE shortened from {prior_wale:g} to {wale:g} years",
                              wale, prior_wale))
    return flags


def _rule_valuation(current, prior, baseline, as_at) -> list[Flag]:
    flags = []
    valuation = _num(current.get("valuation"))
    if prior and valuation is not None:
        prior_valuation = _num(prior.get("valuation"))
        if prior_valuation is not None and prior_valuation > 0 and valuation < prior_valuation:
            decline = (prior_valuation - valuation) / prior_valuation * 100
            severity = Severity.HIGH if decline >= MATERIAL_VALUATION_DROP else Severity.MEDIUM
            flags.append(Flag("valuation_decline", severity, "valuation",
                              f"Valuation fell {decline:.1f}%, from ${prior_valuation:,.0f} "
                              f"to ${valuation:,.0f}", valuation, prior_valuation))

    nta = _num(current.get("nta_per_unit"))
    if prior and nta is not None:
        prior_nta = _num(prior.get("nta_per_unit"))
        if prior_nta is not None and nta < prior_nta:
            flags.append(Flag("nta_decline", Severity.MEDIUM, "nta_per_unit",
                              f"Net assets per unit fell from ${prior_nta:,.0f} to "
                              f"${nta:,.0f}", nta, prior_nta))
    return flags


def _rule_fees_vs_distributions(current, prior, baseline, as_at) -> list[Flag]:
    """Manager taking more while investors receive less.

    Checked on whichever basis both periods share -- dollars or percent of
    scheme property -- because reports use both.
    """
    if not prior:
        return []

    rate = _num(current.get("distribution_rate"))
    prior_rate = _num(prior.get("distribution_rate"))
    unit = _text(current.get("distribution_unit"))
    prior_unit = _text(prior.get("distribution_unit"))

    comparable_rates = (
        rate is not None
        and prior_rate is not None
        and (not (unit and prior_unit) or unit == prior_unit)
    )
    if not comparable_rates or rate >= prior_rate:
        return []

    for column, label in (
        ("manager_fee_total_dollars", "$"),
        ("manager_fee_total_percent", "% of scheme property"),
    ):
        fees = _num(current.get(column))
        prior_fees = _num(prior.get(column))
        if fees is not None and prior_fees is not None and fees > prior_fees:
            return [Flag("fees_up_distributions_down", Severity.HIGH, column,
                         f"Manager fees rose ({prior_fees:,.2f} to {fees:,.2f} {label}) "
                         f"while the distribution rate fell ({prior_rate:g} to {rate:g})",
                         fees, prior_fees)]
    return []


#: Figures whose disappearance is worth reporting. Not every column: a missing
#: source page or note is noise, a missing covenant is not.
WATCHED_FIELDS = [
    "valuation", "nta_per_unit", "total_debt", "lvr_percent", "icr_actual",
    "icr_covenant", "facility_expiry", "occupancy_percent", "vacancy_percent",
    "wale_years", "distribution_rate", "payout_ratio_percent",
    "adjusted_operating_profit", "cash",
]


def _rule_disclosure_withdrawn(current, prior, baseline, as_at) -> list[Flag]:
    """A figure the manager reported last period and does not report now.

    This is the flag the whole schema design serves: because every field is
    required and explicitly null, "stopped reporting" is distinguishable from
    "the extractor missed it".
    """
    if not prior:
        return []
    withdrawn = []
    for name in WATCHED_FIELDS:
        had = _text(prior.get(name)) is not None
        has = _text(current.get(name)) is not None
        if had and not has:
            withdrawn.append(name)
    if not withdrawn:
        return []
    readable = ", ".join(n.replace("_", " ") for n in withdrawn)
    return [Flag("disclosure_withdrawn", Severity.HIGH, ",".join(withdrawn),
                 f"Reported last period but absent this period: {readable}")]


def _rule_figures_in_thousands(current, prior, baseline, as_at) -> list[Flag]:
    """A valuation too small to be a building means the report's table was in thousands.

    Ratios survive this unharmed -- LVR computed from two figures in thousands is still
    correct -- so nothing else in the pipeline notices. Only the dollar figures are
    wrong, and they are wrong by a factor of a thousand.
    """
    valuation = _num(current.get("valuation"))
    if valuation is None or valuation >= IMPLAUSIBLE_VALUATION:
        return []
    debt = _num(current.get("total_debt"))
    detail = f"valuation reads {valuation:,.0f}"
    if debt is not None:
        detail += f" against debt of {debt:,.0f}"
    return [Flag("figures_may_be_in_thousands", Severity.HIGH, "valuation",
                 f"{detail} -- too small for a property syndicate, so the report's table was "
                 "probably printed in thousands and the dollar figures are 1,000x too low",
                 valuation)]


def _rule_extraction_quality(current, prior, baseline, as_at) -> list[Flag]:
    """Low completeness usually means a scanned PDF or the wrong document."""
    found = _num(current.get("completeness_found"))
    total = _num(current.get("completeness_total"))
    if found is None or not total:
        return []
    if found == 0:
        return [Flag("extraction_empty", Severity.CRITICAL, "completeness_found",
                     "No figures were extracted from this document at all", found)]
    if found < total / 2:
        return [Flag("extraction_sparse", Severity.MEDIUM, "completeness_found",
                     f"Only {found:.0f} of {total:.0f} figures were found; the document may "
                     "be image-only or may not be an investor report", found)]
    return []


def _rule_hedging_policy(current, prior, baseline, as_at) -> list[Flag]:
    """The SIPO's hedging minimum against what the report discloses.

    A written policy tested against a disclosed fact, per syndicate, by name. The
    hedged share is computed only where BOTH the live swap notional and total debt
    are stated. It reads `live_swap_notional`, not `total_swap_notional`: the latter
    sums swaps that have already rolled off, which is how a syndicate reads as "179%
    hedged" against its own debt. A blank there means unknown, never zero.
    """
    if not baseline:
        return []
    minimum = _num(baseline.get("hedging_minimum_percent"))
    if minimum is None:
        return []

    notional = _num(current.get("live_swap_notional"))
    debt = _num(current.get("total_debt"))
    if notional is not None and debt:
        hedged = notional / debt * 100
        if hedged > HEDGE_SHARE_IMPLAUSIBLE:
            # More notional than debt means the swaps are staggered or forward-starting:
            # one starts as another ends. The schema records expiry dates but no start
            # dates, so a point-in-time hedged share cannot be computed -- and reporting
            # "complies" from a sum like that would be worse than reporting nothing.
            return [Flag("hedging_policy_unverifiable", Severity.MEDIUM, "live_swap_notional",
                         f"Live swap notional is {hedged:.0f}% of debt, so the swaps are "
                         f"staggered or forward-starting; the share hedged at any one time "
                         f"cannot be computed, and the SIPO commits to {minimum:.0f}%")]
        if hedged < minimum:
            return [Flag("hedging_below_policy", Severity.HIGH, "total_swap_notional",
                         f"{hedged:.0f}% of debt hedged, against the SIPO's "
                         f"{minimum:.0f}% minimum", round(hedged, 1))]
        return []

    expiry = _date(current.get("earliest_swap_expiry"))
    detail = (f"the earliest swap expired {expiry:%d %b %Y}"
              if expiry is not None and _months_between(as_at, expiry) < 0
              else "no live swap notional is disclosed")
    return [Flag("hedging_policy_unverifiable", Severity.MEDIUM, "total_swap_notional",
                 f"SIPO commits to hedging at least {minimum:.0f}% of debt; {detail}, "
                 "so compliance cannot be verified from this report")]


def _rule_occupancy_policy(current, prior, baseline, as_at) -> list[Flag]:
    """Occupancy against the floor the SIPO itself states."""
    if not baseline:
        return []
    floor = _num(baseline.get("occupancy_floor_percent"))
    occupancy = _num(current.get("occupancy_percent"))
    if floor is None or occupancy is None:
        return []
    if occupancy < floor:
        return [Flag("occupancy_below_policy", Severity.HIGH, "occupancy_percent",
                     f"Occupancy {occupancy:.1f}% is below the SIPO's {floor:.0f}% floor",
                     occupancy)]
    return []


def _rule_nta_policy(current, prior, baseline, as_at) -> list[Flag]:
    """NTA per unit against the SIPO's floor, which is a share of NTA at acquisition.

    Dormant until `formation_nav_per_unit` is filled. The floor is a percentage OF the
    formation NTA, and `original_investment_per_unit` is not the same figure -- issue
    costs mean a $50,000 unit starts below $50,000 of NTA -- so substituting it would
    manufacture breaches.
    """
    if not baseline:
        return []
    floor_percent = _num(baseline.get("nta_floor_percent"))
    formation = _num(baseline.get("formation_nav_per_unit"))
    nta = _num(current.get("nta_per_unit"))
    if floor_percent is None or not formation or nta is None:
        return []
    floor = formation * floor_percent / 100
    if nta < floor:
        share = nta / formation * 100
        return [Flag("nta_below_policy", Severity.HIGH, "nta_per_unit",
                     f"NTA {nta:,.2f} per unit is {share:.0f}% of the {formation:,.2f} at "
                     f"formation, below the SIPO's {floor_percent:.0f}% floor", nta)]
    return []


RULES = [
    _rule_facility_expiry,
    _rule_swap_expiry,
    _rule_icr,
    _rule_lvr,
    _rule_hedging_policy,
    _rule_occupancy_policy,
    _rule_nta_policy,
    _rule_distribution,
    _rule_tenancy,
    _rule_valuation,
    _rule_fees_vs_distributions,
    _rule_disclosure_withdrawn,
    _rule_extraction_quality,
    _rule_figures_in_thousands,
]


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

@dataclass
class SyndicateReview:
    """All flags for one syndicate for one period, ranked."""

    syndicate_id: str
    period_end: str
    flags: list = dataclass_field(default_factory=list)

    @property
    def worst(self) -> Severity:
        return max((f.severity for f in self.flags), default=Severity.INFO)

    @property
    def needs_attention(self) -> bool:
        return self.worst >= Severity.HIGH


def evaluate(current: dict, prior: dict | None = None,
             baseline: dict | None = None, as_at: date | None = None) -> list[Flag]:
    """Run every rule over one period row. Returns flags, most serious first."""
    as_at = as_at or date.today()
    flags = []
    for rule in RULES:
        flags.extend(rule(current, prior, baseline, as_at))
    flags.sort(key=lambda f: (-f.severity, f.code))
    return flags


def prior_period(rows: list[dict], syndicate_id: str, period_end: str) -> dict | None:
    """The most recent stored row for this syndicate before `period_end`."""
    earlier = [
        r for r in rows
        if str(r.get("syndicate_id")) == str(syndicate_id)
        and _text(r.get("period_end"))
        and str(r.get("period_end")) < str(period_end)
    ]
    if not earlier:
        return None
    return max(earlier, key=lambda r: str(r.get("period_end")))


def review_all(period_rows: list[dict], baseline_rows: list[dict] | None = None,
               as_at: date | None = None) -> list[SyndicateReview]:
    """Review the latest period of every syndicate, worst first.

    This is the ranked worklist: read the top of it, not all thirty documents.
    """
    baseline_by_id = {
        str(b.get("syndicate_id")): b for b in (baseline_rows or []) if b.get("syndicate_id")
    }

    latest: dict[str, dict] = {}
    for row in period_rows:
        sid = str(row.get("syndicate_id") or "")
        period = str(row.get("period_end") or "")
        if not sid or not period:
            continue
        if sid not in latest or period > str(latest[sid].get("period_end") or ""):
            latest[sid] = row

    reviews = []
    for sid, row in latest.items():
        period = str(row.get("period_end"))
        reviews.append(SyndicateReview(
            syndicate_id=sid,
            period_end=period,
            flags=evaluate(row, prior_period(period_rows, sid, period),
                           baseline_by_id.get(sid), as_at),
        ))

    reviews.sort(key=lambda r: (-r.worst, -len(r.flags), r.syndicate_id))
    return reviews
