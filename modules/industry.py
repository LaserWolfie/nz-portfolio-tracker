"""External benchmarks: listed NZ property vehicles and valuer sector data.

**Plain Python, no LLM.** Reads the `Industry_Benchmarks` tab and compares the
portfolio against it.

Both external sources -- a listed vehicle's published metrics and a valuer's
sector series -- land in one tab keyed by (metric, sector, period_end, source),
so this module reads one mechanism rather than two.

Three rules, and the first two are refusals.

**Never compare across sectors.** An Office cap rate and a childcare cap rate
differ for good reasons. A benchmark applies to a syndicate only when the sectors
match, or when the benchmark is explicitly marked "All".

**Never compare across a basis mismatch.** A listed REIT's gearing is measured on
a different footing from a single-asset syndicate's LVR, and a portfolio WALE is
not a single building's. `basis_notes` travels into every comparison so the
reader can see how the figure was built, and a benchmark without it is rejected
rather than used.

**Provenance is mandatory.** `source` and `basis_notes` are required. A benchmark
gets used in an argument with a manager, which is precisely when an unsourced
number becomes worthless.
"""

from dataclasses import dataclass

from modules.benchmarks import METRICS_BY_KEY, Holding
from modules.deltas import _num, _text

#: A benchmark marked with this sector applies to every syndicate.
ALL_SECTORS = "all"

VALID_SOURCE_TYPES = {"listed_vehicle", "valuer", "index", "other"}


@dataclass(frozen=True)
class Benchmark:
    """One external reference figure, with how it was measured."""

    metric: str
    sector: str
    region: str
    period_end: str
    value: float
    unit: str
    source: str
    source_type: str
    basis_notes: str
    url: str = ""

    @property
    def applies_to_all_sectors(self) -> bool:
        return self.sector.strip().lower() == ALL_SECTORS


@dataclass(frozen=True)
class Comparison:
    """One syndicate measured against one external benchmark."""

    syndicate_id: str
    name: str
    metric: str
    portfolio_value: float
    benchmark: Benchmark

    @property
    def difference(self) -> float:
        return self.portfolio_value - self.benchmark.value

    @property
    def is_better(self) -> bool:
        metric = METRICS_BY_KEY[self.metric]
        return (self.difference > 0) if metric.higher_is_better else (self.difference < 0)


class BenchmarkError(Exception):
    """Raised when a benchmark row cannot be trusted enough to use."""


def parse_benchmarks(rows: list[dict], strict: bool = False) -> list[Benchmark]:
    """Turn sheet rows into benchmarks, dropping any that cannot be defended.

    A row is rejected when it names an unknown metric, carries no value, or
    lacks a source or basis. With `strict`, rejection raises instead of
    skipping -- useful when loading a file someone has just edited.
    """
    out = []
    for row in rows:
        metric = _text(row.get("metric"))
        value = _num(row.get("value"))
        source = _text(row.get("source"))
        basis = _text(row.get("basis_notes"))

        problem = None
        if not metric or metric not in METRICS_BY_KEY:
            problem = f"unknown metric {metric!r}"
        elif value is None:
            problem = "no value"
        elif not source:
            problem = "no source"
        elif not basis:
            problem = "no basis_notes, so comparability cannot be judged"

        if problem:
            if strict:
                raise BenchmarkError(f"{metric or '?'} / {source or '?'}: {problem}")
            continue

        out.append(Benchmark(
            metric=metric,
            sector=_text(row.get("sector")) or ALL_SECTORS,
            region=_text(row.get("region")) or "NZ",
            period_end=_text(row.get("period_end")) or "",
            value=value,
            unit=_text(row.get("unit")) or "",
            source=source,
            source_type=(_text(row.get("source_type")) or "other").lower(),
            basis_notes=basis,
            url=_text(row.get("url")) or "",
        ))
    return out


def applicable(benchmarks: list[Benchmark], holding: Holding, metric_key: str,
               as_at: str | None = None) -> Benchmark | None:
    """The benchmark that may fairly be applied to this holding, or None.

    Requires the metric to match and the sector to match (or the benchmark to be
    marked "All"). Where several qualify, the one closest to `as_at` without
    being in the future wins -- comparing this year's figure against a benchmark
    published later would be hindsight.
    """
    sector = holding.sector.strip().lower()
    candidates = [
        b for b in benchmarks
        if b.metric == metric_key
        and (b.applies_to_all_sectors or b.sector.strip().lower() == sector)
    ]
    if not candidates:
        return None

    as_at = as_at or holding.period_end
    not_future = [b for b in candidates if b.period_end and b.period_end <= as_at]
    pool = not_future or [b for b in candidates if not b.period_end]
    if not pool:
        return None
    # Sector-specific beats "All"; then the most recent that is not in the future.
    pool.sort(key=lambda b: (b.applies_to_all_sectors, b.period_end), reverse=True)
    pool.sort(key=lambda b: b.applies_to_all_sectors)
    return max(pool, key=lambda b: (not b.applies_to_all_sectors, b.period_end))


def compare_cohort(cohort: list[Holding], benchmarks: list[Benchmark],
                   metric_keys: list[str] | None = None) -> list[Comparison]:
    """Every holding against every benchmark that fairly applies to it."""
    keys = metric_keys or list(METRICS_BY_KEY)
    out = []
    for holding in cohort:
        for key in keys:
            metric = METRICS_BY_KEY.get(key)
            if metric is None:
                continue
            value = holding.value(metric)
            if value is None:
                continue
            bench = applicable(benchmarks, holding, key)
            if bench is None:
                continue
            out.append(Comparison(holding.syndicate_id, holding.name, key, value, bench))
    return out


def coverage(cohort: list[Holding], benchmarks: list[Benchmark]) -> list[tuple[str, int, int]]:
    """Per metric, how many holdings have a benchmark that applies.

    The honest answer to "how well can we benchmark this?" before any conclusion
    is drawn from a thin reference set.
    """
    rows = []
    for key in METRICS_BY_KEY:
        covered = sum(
            1 for h in cohort
            if h.value(METRICS_BY_KEY[key]) is not None and applicable(benchmarks, h, key)
        )
        measurable = sum(1 for h in cohort if h.value(METRICS_BY_KEY[key]) is not None)
        rows.append((key, covered, measurable))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def load(spreadsheet=None) -> list[Benchmark]:
    """Read the `Industry_Benchmarks` tab."""
    from modules import sheets, storage

    spreadsheet = spreadsheet or sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    return parse_benchmarks(
        spreadsheet.worksheet(storage.INDUSTRY_WORKSHEET).get_all_records())
