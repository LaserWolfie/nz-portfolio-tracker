"""Peer benchmarking across the portfolio.

**Plain Python, no LLM**, same discipline as `deltas`: every number here must be
reproducible from stored rows.

The portfolio is its own peer set. That needs no external data, and because
several syndicates share a manager it also allows the same manager to be
compared across their own funds -- which is the sharpest available test of
whether a manager is creating value or simply collecting fees.

Three things this module is careful about.

**Only compare what is comparable.** A debt fund has no cap rate; an Office
tower and a childcare centre have different ones for good reason. Non-property
holdings are excluded outright, and sector-relative ranks are computed alongside
portfolio-wide ones wherever the sector cohort is large enough to mean anything.

**Absolute dollars are not comparable.** A $16,735 capex spend means one thing on
a $2m property and another on a $115m one, so money figures are normalised
against valuation before ranking.

**Say how small the sample is.** With ten syndicates a "bottom quartile" is two
or three of them. Every ranking carries its `n` so it cannot be read as more than
it is.
"""

from dataclasses import dataclass, field
from statistics import median

from modules.deltas import _num, _text

#: Sector labels that are not property and must not be ranked against it.
NON_PROPERTY_SECTORS = {"private debt", "debt / equity fund", "debt", "cash"}

#: A sector cohort smaller than this is too small to rank within.
MIN_SECTOR_COHORT = 3


@dataclass(frozen=True)
class Metric:
    """One comparable measure, and which end of it is good."""

    key: str
    label: str
    higher_is_better: bool
    #: Divide by this column and express as a percent, for figures in dollars
    #: whose size depends on the syndicate rather than its quality.
    normalise_by: str | None = None
    unit: str = ""
    note: str = ""


METRICS = [
    Metric("lvr_percent", "LVR", False, unit="%"),
    Metric("icr_actual", "Interest cover", True, unit="x"),
    Metric("wale_years", "WALE", True, unit=" yrs"),
    Metric("occupancy_percent", "Occupancy", True, unit="%"),
    Metric("vacancy_percent", "Vacancy", False, unit="%"),
    Metric("payout_ratio_percent", "Payout ratio", False, unit="%",
           note="above 100% means distributions exceeded earnings"),
    Metric("rent_reversion_percent", "Rent reversion", False, unit="%",
           note="positive means passing rent is above market, so rents fall as leases roll"),
    Metric("capitalisation_rate_percent", "Cap rate", True, unit="%",
           note="a cap rate well below peers means a more generous valuation"),
    Metric("manager_fee_total_percent", "Manager fees", False, unit="% of scheme property"),
    Metric("capex_spent", "Capex", True, normalise_by="valuation", unit="% of valuation",
           note="reinvestment in the asset"),
    Metric("cash", "Cash", True, normalise_by="valuation", unit="% of valuation"),
    Metric("lease_incentives_paid", "Lease incentives", False, normalise_by="valuation",
           unit="% of valuation",
           note="high incentives can mean headline rents are sustained by giving value back"),
]

METRICS_BY_KEY = {m.key: m for m in METRICS}


@dataclass
class Holding:
    """One syndicate's latest stored period, with its baseline context."""

    syndicate_id: str
    name: str
    manager: str
    sector: str
    period_end: str
    row: dict = field(repr=False, default_factory=dict)

    def value(self, metric: Metric):
        """The metric for this holding, normalised where the spec says so."""
        raw = _num(self.row.get(metric.key))
        if raw is None:
            return None
        if metric.normalise_by:
            base = _num(self.row.get(metric.normalise_by))
            if not base:
                return None
            return raw / base * 100
        return raw


@dataclass
class Rank:
    """Where one holding sits on one metric, among how many."""

    syndicate_id: str
    name: str
    value: float
    position: int          # 1 = best
    n: int
    quartile: int          # 1 = best quartile, 4 = worst

    @property
    def is_worst_quartile(self) -> bool:
        return self.quartile == 4


def build_cohort(period_rows: list[dict], baseline_rows: list[dict]) -> list[Holding]:
    """The latest stored period for every comparable property syndicate.

    Excludes non-property holdings and anything the baseline marks sold: a debt
    fund has no cap rate, and a sold syndicate is not a peer.
    """
    baseline = {str(b.get("syndicate_id")): b for b in baseline_rows if b.get("syndicate_id")}

    latest: dict[str, dict] = {}
    for row in period_rows:
        sid = str(row.get("syndicate_id") or "")
        period = str(row.get("period_end") or "")
        if not sid or not period:
            continue
        if sid not in latest or period > str(latest[sid].get("period_end") or ""):
            latest[sid] = row

    cohort = []
    for sid, row in latest.items():
        base = baseline.get(sid, {})
        sector = str(base.get("sector", "") or "")
        status = str(base.get("register_status", "") or "")
        if sector.strip().lower() in NON_PROPERTY_SECTORS:
            continue
        if "sold" in status.lower():
            continue
        cohort.append(Holding(
            syndicate_id=sid,
            name=_text(base.get("canonical_name")) or sid,
            manager=_text(base.get("manager_name")) or "Unknown",
            sector=sector or "Unspecified",
            period_end=str(row.get("period_end")),
            row=row,
        ))
    cohort.sort(key=lambda h: h.name)
    return cohort


def _quartile(position: int, n: int) -> int:
    """1 is the best quarter, 4 the worst. Positions are 1-based."""
    if n <= 1:
        return 1
    return min(4, int((position - 1) / n * 4) + 1)


def rank_metric(cohort: list[Holding], metric: Metric) -> list[Rank]:
    """Every holding that discloses this metric, best first.

    Holdings that do not disclose it are absent rather than ranked last --
    not reporting a figure is a separate finding, and the delta engine already
    raises it.
    """
    scored = [(h, h.value(metric)) for h in cohort]
    scored = [(h, v) for h, v in scored if v is not None]
    scored.sort(key=lambda pair: pair[1], reverse=metric.higher_is_better)

    n = len(scored)
    return [
        Rank(h.syndicate_id, h.name, v, position, n, _quartile(position, n))
        for position, (h, v) in enumerate(scored, start=1)
    ]


def rank_table(cohort: list[Holding]) -> dict[str, list[Rank]]:
    """Every metric ranked across the whole cohort."""
    return {m.key: rank_metric(cohort, m) for m in METRICS}


def sector_ranks(cohort: list[Holding], metric: Metric) -> dict[str, list[Rank]]:
    """Ranks computed within each sector, where the sector is big enough.

    An Office cap rate and a childcare cap rate differ for good reasons, so a
    portfolio-wide ranking of cap rate says less than it appears to.
    """
    by_sector: dict[str, list[Holding]] = {}
    for holding in cohort:
        by_sector.setdefault(holding.sector, []).append(holding)
    return {
        sector: rank_metric(group, metric)
        for sector, group in by_sector.items()
        if len(group) >= MIN_SECTOR_COHORT
    }


def scorecard(cohort: list[Holding], syndicate_id: str) -> list[tuple[Metric, Rank]]:
    """How one syndicate ranks on every metric it discloses."""
    out = []
    for metric in METRICS:
        for rank in rank_metric(cohort, metric):
            if rank.syndicate_id == syndicate_id:
                out.append((metric, rank))
                break
    return out


def worst_quartile_count(cohort: list[Holding]) -> list[tuple[Holding, int, int]]:
    """Holdings by how many metrics they sit in the worst quartile on.

    One bad metric is a fact about a property. Several at once is a fact about
    how it is being run.
    """
    counts: dict[str, int] = {}
    disclosed: dict[str, int] = {}
    for metric in METRICS:
        for rank in rank_metric(cohort, metric):
            disclosed[rank.syndicate_id] = disclosed.get(rank.syndicate_id, 0) + 1
            if rank.is_worst_quartile:
                counts[rank.syndicate_id] = counts.get(rank.syndicate_id, 0) + 1

    rows = [(h, counts.get(h.syndicate_id, 0), disclosed.get(h.syndicate_id, 0))
            for h in cohort]
    rows.sort(key=lambda r: (-r[1], r[0].name))
    return rows


@dataclass
class ManagerSummary:
    """One manager, across every fund of theirs in the portfolio."""

    manager: str
    holdings: int
    medians: dict            # metric key -> median value, or None
    worst_quartile_total: int
    disclosure_rate: float   # share of metrics disclosed across their funds


def manager_scorecards(cohort: list[Holding]) -> list[ManagerSummary]:
    """Compare managers across their own funds.

    Where a manager runs several syndicates, their medians say more than any one
    fund does -- and a low disclosure rate across all of them is a finding in
    itself, because it is a choice the manager makes, not a property of the
    building.
    """
    by_manager: dict[str, list[Holding]] = {}
    for holding in cohort:
        by_manager.setdefault(holding.manager, []).append(holding)

    worst = {h.syndicate_id: count for h, count, _ in worst_quartile_count(cohort)}

    summaries = []
    for manager, group in by_manager.items():
        medians = {}
        disclosed = total = 0
        for metric in METRICS:
            values = [h.value(metric) for h in group]
            present = [v for v in values if v is not None]
            medians[metric.key] = median(present) if present else None
            disclosed += len(present)
            total += len(values)
        summaries.append(ManagerSummary(
            manager=manager,
            holdings=len(group),
            medians=medians,
            worst_quartile_total=sum(worst.get(h.syndicate_id, 0) for h in group),
            disclosure_rate=(disclosed / total) if total else 0.0,
        ))
    summaries.sort(key=lambda s: (-s.holdings, s.manager))
    return summaries


def disclosure_gaps(cohort: list[Holding]) -> list[tuple[Metric, int, int]]:
    """Metrics ranked by how often they are NOT disclosed.

    A figure missing from one report is an oversight. The same figure missing
    from most of them is an industry norm worth challenging.
    """
    rows = []
    for metric in METRICS:
        missing = sum(1 for h in cohort if h.value(metric) is None)
        rows.append((metric, missing, len(cohort)))
    rows.sort(key=lambda r: -r[1])
    return rows
