"""Tests for external benchmarks. No API calls, no network.

Weighted toward the refusals: an unfair comparison is worse than no comparison,
because it will be used in an argument with a manager.
"""

from modules.benchmarks import build_cohort
from modules.industry import (
    Benchmark,
    BenchmarkError,
    applicable,
    applicable_all,
    compare_cohort,
    coverage,
    parse_benchmarks,
    spread,
)

import pytest


def _row(**over):
    row = {
        "metric": "capitalisation_rate_percent", "sector": "Office", "region": "Auckland",
        "period_end": "2026-03-31", "value": 6.5, "unit": "%",
        "source": "Example Valuer NZ Office Q1 2026", "source_type": "valuer",
        "basis_notes": "prime Auckland CBD office, adopted market cap rate", "url": "",
    }
    row.update(over)
    return row


def _cohort(sector="Office", **cols):
    period = {"syndicate_id": "A", "period_end": "2026-03-31"}
    period.update(cols)
    return build_cohort([period], [{"syndicate_id": "A", "canonical_name": "A Tower",
                                    "manager_name": "Centuria", "sector": sector}])


class TestProvenanceIsMandatory:
    def test_a_benchmark_without_a_source_is_rejected(self):
        assert parse_benchmarks([_row(source="")]) == []

    def test_a_benchmark_without_a_basis_is_rejected(self):
        """Without knowing how it was measured, comparability cannot be judged."""
        assert parse_benchmarks([_row(basis_notes="")]) == []

    def test_a_benchmark_with_no_value_is_rejected(self):
        assert parse_benchmarks([_row(value="")]) == []

    def test_an_unknown_metric_is_rejected(self):
        assert parse_benchmarks([_row(metric="vibes")]) == []

    def test_strict_mode_says_why(self):
        with pytest.raises(BenchmarkError, match="basis_notes"):
            parse_benchmarks([_row(basis_notes="")], strict=True)

    def test_a_complete_row_survives(self):
        benchmarks = parse_benchmarks([_row()])
        assert len(benchmarks) == 1
        assert benchmarks[0].value == 6.5
        assert "Auckland" in benchmarks[0].basis_notes


class TestSectorDiscipline:
    def test_a_sector_benchmark_does_not_apply_across_sectors(self):
        """An Office cap rate says nothing about a childcare centre."""
        cohort = _cohort(sector="Childcare", capitalisation_rate_percent=7.5)
        benchmarks = parse_benchmarks([_row(sector="Office")])
        assert applicable(benchmarks, cohort[0], "capitalisation_rate_percent") is None

    def test_a_matching_sector_applies(self):
        cohort = _cohort(sector="Office", capitalisation_rate_percent=6.38)
        benchmarks = parse_benchmarks([_row(sector="Office")])
        assert applicable(benchmarks, cohort[0], "capitalisation_rate_percent") is not None

    def test_all_sectors_applies_to_anything(self):
        cohort = _cohort(sector="Childcare", capitalisation_rate_percent=7.5)
        benchmarks = parse_benchmarks([_row(sector="All")])
        assert applicable(benchmarks, cohort[0], "capitalisation_rate_percent") is not None

    def test_a_sector_specific_benchmark_beats_an_all_sectors_one(self):
        cohort = _cohort(sector="Office", capitalisation_rate_percent=6.38)
        benchmarks = parse_benchmarks([
            _row(sector="All", value=7.0, source="Broad index"),
            _row(sector="Office", value=6.5, source="Office series"),
        ])
        chosen = applicable(benchmarks, cohort[0], "capitalisation_rate_percent")
        assert chosen.source == "Office series"


class TestPeriodDiscipline:
    def test_a_future_benchmark_is_not_used(self):
        """Comparing FY2026 against a figure published later is hindsight."""
        cohort = _cohort(capitalisation_rate_percent=6.38)
        benchmarks = parse_benchmarks([_row(period_end="2027-03-31", source="Later")])
        assert applicable(benchmarks, cohort[0], "capitalisation_rate_percent") is None

    def test_the_most_recent_past_benchmark_wins(self):
        cohort = _cohort(capitalisation_rate_percent=6.38)
        benchmarks = parse_benchmarks([
            _row(period_end="2024-03-31", value=5.8, source="Older"),
            _row(period_end="2026-03-31", value=6.5, source="Current"),
        ])
        assert applicable(benchmarks, cohort[0], "capitalisation_rate_percent").source == "Current"


class TestComparison:
    def test_direction_follows_the_metric(self):
        """Lower LVR is better; higher cap rate is the more conservative valuation."""
        cohort = _cohort(lvr_percent=35.0)
        benchmarks = parse_benchmarks([_row(metric="lvr_percent", value=40.0, unit="%")])
        result = compare_cohort(cohort, benchmarks, ["lvr_percent"])[0]
        assert result.difference == -5.0
        assert result.is_better

    def test_worse_than_benchmark_is_reported_as_such(self):
        cohort = _cohort(lvr_percent=48.0)
        benchmarks = parse_benchmarks([_row(metric="lvr_percent", value=40.0, unit="%")])
        assert not compare_cohort(cohort, benchmarks, ["lvr_percent"])[0].is_better

    def test_the_basis_travels_with_the_comparison(self):
        cohort = _cohort(lvr_percent=35.0)
        benchmarks = parse_benchmarks([
            _row(metric="lvr_percent", value=40.0,
                 basis_notes="listed REIT gearing on total assets, not single-asset LVR")])
        result = compare_cohort(cohort, benchmarks, ["lvr_percent"])[0]
        assert "not single-asset LVR" in result.benchmark.basis_notes

    def test_undisclosed_portfolio_values_are_not_compared(self):
        cohort = _cohort(lvr_percent="")
        benchmarks = parse_benchmarks([_row(metric="lvr_percent", value=40.0)])
        assert compare_cohort(cohort, benchmarks, ["lvr_percent"]) == []


class TestCoverage:
    def test_reports_how_much_of_the_cohort_can_be_benchmarked(self):
        cohort = _cohort(capitalisation_rate_percent=6.38, lvr_percent=46.0)
        benchmarks = parse_benchmarks([_row(metric="capitalisation_rate_percent")])
        by_metric = dict((k, (c, m)) for k, c, m in coverage(cohort, benchmarks))
        assert by_metric["capitalisation_rate_percent"] == (1, 1)
        assert by_metric["lvr_percent"] == (0, 1), "measurable but no benchmark to compare to"

    def test_an_empty_benchmark_tab_yields_no_coverage(self):
        cohort = _cohort(lvr_percent=46.0)
        assert all(covered == 0 for _, covered, _ in coverage(cohort, []))


class TestEveryApplicableBenchmark:
    """One winner can mislead. Goodman's 19.8% gearing is the newest industrial
    reference and follows $700m of asset sales; shown alone it makes every industrial
    syndicate look 20-29 points worse than a fairer comparator would."""

    GEARING = [
        _row(metric="lvr_percent", sector="Industrial", period_end="2026-03-31", value=19.8,
             source="Goodman NZ Annual Report 2026", basis_notes="look-through LVR"),
        _row(metric="lvr_percent", sector="Industrial", period_end="2025-12-31", value=34.2,
             source="PFI FY26 Interim Report", basis_notes="gearing at 31 Dec 2025"),
        _row(metric="lvr_percent", sector="All", period_end="2025-09-30", value=35.9,
             source="Argosy FY26 Interim", basis_notes="debt to total assets"),
    ]

    def _cohort_industrial(self):
        return _cohort(sector="Industrial", lvr_percent=45.7)

    def test_all_sources_reports_every_reference(self):
        benchmarks = parse_benchmarks(self.GEARING)
        one = compare_cohort(self._cohort_industrial(), benchmarks, ["lvr_percent"])
        many = compare_cohort(self._cohort_industrial(), benchmarks, ["lvr_percent"],
                              all_sources=True)
        assert len(one) == 1, "the default stays a single winner"
        assert {c.benchmark.value for c in many} == {19.8, 34.2, 35.9}

    def test_sector_specific_comes_before_all_sectors(self):
        got = applicable_all(parse_benchmarks(self.GEARING),
                             self._cohort_industrial()[0], "lvr_percent")
        assert [b.sector for b in got] == ["Industrial", "Industrial", "All"]

    def test_within_a_sector_the_newest_is_listed_first(self):
        got = applicable_all(parse_benchmarks(self.GEARING),
                             self._cohort_industrial()[0], "lvr_percent")
        assert [b.period_end for b in got[:2]] == ["2026-03-31", "2025-12-31"]

    def test_a_future_benchmark_is_still_excluded(self):
        rows = self.GEARING + [
            _row(metric="lvr_percent", sector="Industrial", period_end="2026-06-30", value=34.2,
                 source="PFI FY26 Annual", basis_notes="gearing at 30 Jun 2026")
        ]
        got = applicable_all(parse_benchmarks(rows), self._cohort_industrial()[0], "lvr_percent")
        assert "2026-06-30" not in [b.period_end for b in got], "hindsight is still refused"

    def test_a_cross_sector_benchmark_is_still_excluded(self):
        rows = self.GEARING + [
            _row(metric="lvr_percent", sector="Childcare", period_end="2026-01-31", value=60.0,
                 source="Childcare survey", basis_notes="childcare gearing")
        ]
        got = applicable_all(parse_benchmarks(rows), self._cohort_industrial()[0], "lvr_percent")
        assert "Childcare" not in [b.sector for b in got]

    def test_spread_shows_how_much_the_single_winner_was_doing(self):
        comparisons = compare_cohort(self._cohort_industrial(), parse_benchmarks(self.GEARING),
                                     ["lvr_percent"], all_sources=True)
        summary = spread(comparisons)[("A", "lvr_percent")]
        assert summary["n_benchmarks"] == 3
        assert (summary["low"], summary["high"]) == (19.8, 35.9)
        assert summary["portfolio_value"] == 45.7
        assert summary["better_against"] == 0, "45.7% gearing beats none of the three"
