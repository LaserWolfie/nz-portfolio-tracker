"""Tests for peer benchmarking. No API calls, no network."""

import pytest

from modules.benchmarks import (
    METRICS_BY_KEY,
    Holding,
    build_cohort,
    disclosure_gaps,
    manager_scorecards,
    rank_metric,
    scorecard,
    sector_ranks,
    worst_quartile_count,
)


def _period(sid, **cols):
    row = {"syndicate_id": sid, "period_end": "2026-03-31"}
    row.update(cols)
    return row


def _base(sid, name, manager="Centuria", sector="Office", **extra):
    row = {"syndicate_id": sid, "canonical_name": name,
           "manager_name": manager, "sector": sector}
    row.update(extra)
    return row


class TestCohort:
    def test_debt_funds_are_excluded(self):
        """A debt fund has no cap rate; ranking it against property is nonsense."""
        cohort = build_cohort(
            [_period("A"), _period("MERX")],
            [_base("A", "Office Trust"), _base("MERX", "Merx", sector="Debt / equity fund")],
        )
        assert [h.syndicate_id for h in cohort] == ["A"]

    def test_sold_holdings_are_excluded(self):
        cohort = build_cohort(
            [_period("A"), _period("GONE")],
            [_base("A", "Office Trust"),
             _base("GONE", "Preston Road", register_status="SOLD - recently disposed")],
        )
        assert [h.syndicate_id for h in cohort] == ["A"]

    def test_only_the_latest_period_is_used(self):
        cohort = build_cohort(
            [{"syndicate_id": "A", "period_end": "2025-03-31", "lvr_percent": 60},
             {"syndicate_id": "A", "period_end": "2026-03-31", "lvr_percent": 40}],
            [_base("A", "Office Trust")],
        )
        assert len(cohort) == 1
        assert cohort[0].period_end == "2026-03-31"
        assert cohort[0].value(METRICS_BY_KEY["lvr_percent"]) == 40


class TestDirection:
    def _cohort(self, key, values):
        periods = [_period(f"S{i}", **{key: v}) for i, v in enumerate(values)]
        bases = [_base(f"S{i}", f"Syndicate {i}") for i in range(len(values))]
        return build_cohort(periods, bases)

    def test_lower_lvr_ranks_better(self):
        ranks = rank_metric(self._cohort("lvr_percent", [60, 30, 45]),
                            METRICS_BY_KEY["lvr_percent"])
        assert [r.value for r in ranks] == [30, 45, 60]
        assert ranks[0].position == 1

    def test_higher_wale_ranks_better(self):
        ranks = rank_metric(self._cohort("wale_years", [2.0, 8.0, 4.0]),
                            METRICS_BY_KEY["wale_years"])
        assert [r.value for r in ranks] == [8.0, 4.0, 2.0]

    def test_lower_rent_reversion_ranks_better(self):
        """Positive reversion means rents fall as leases roll."""
        ranks = rank_metric(self._cohort("rent_reversion_percent", [28.4, -5.0, 0.0]),
                            METRICS_BY_KEY["rent_reversion_percent"])
        assert ranks[0].value == -5.0
        assert ranks[-1].value == 28.4

    def test_lower_fees_rank_better(self):
        ranks = rank_metric(self._cohort("manager_fee_total_percent", [0.9, 0.4, 0.6]),
                            METRICS_BY_KEY["manager_fee_total_percent"])
        assert ranks[0].value == 0.4


class TestNormalisation:
    def test_money_is_normalised_against_valuation(self):
        """$16,735 of capex means different things on $2m and $115m."""
        cohort = build_cohort(
            [_period("BIG", capex_spent=16735, valuation=115_000_000),
             _period("SMALL", capex_spent=16735, valuation=2_000_000)],
            [_base("BIG", "Big Tower"), _base("SMALL", "Small Shed")],
        )
        ranks = rank_metric(cohort, METRICS_BY_KEY["capex_spent"])
        assert ranks[0].syndicate_id == "SMALL", "same dollars, far higher share of value"
        assert ranks[0].value == pytest.approx(0.837, abs=0.01)

    def test_missing_denominator_yields_no_value(self):
        cohort = build_cohort([_period("A", capex_spent=1000)], [_base("A", "A")])
        assert cohort[0].value(METRICS_BY_KEY["capex_spent"]) is None


class TestUndisclosedIsNotRankedLast:
    def test_absent_figures_are_omitted_not_scored(self):
        """Not disclosing is a separate finding; it must not read as a good or bad score."""
        cohort = build_cohort(
            [_period("A", icr_actual=2.5), _period("B"), _period("C", icr_actual=1.2)],
            [_base("A", "A"), _base("B", "B"), _base("C", "C")],
        )
        ranks = rank_metric(cohort, METRICS_BY_KEY["icr_actual"])
        assert [r.syndicate_id for r in ranks] == ["A", "C"]
        assert all(r.n == 2 for r in ranks), "n reflects who disclosed, not cohort size"

    def test_blank_string_is_not_zero(self):
        cohort = build_cohort([_period("A", lvr_percent="")], [_base("A", "A")])
        assert cohort[0].value(METRICS_BY_KEY["lvr_percent"]) is None


class TestSmallSampleHonesty:
    def test_every_rank_carries_its_n(self):
        cohort = build_cohort(
            [_period(f"S{i}", lvr_percent=40 + i) for i in range(3)],
            [_base(f"S{i}", f"S{i}") for i in range(3)],
        )
        ranks = rank_metric(cohort, METRICS_BY_KEY["lvr_percent"])
        assert all(r.n == 3 for r in ranks)

    def test_sector_ranks_skip_cohorts_that_are_too_small(self):
        cohort = build_cohort(
            [_period(f"O{i}", lvr_percent=40 + i) for i in range(3)] + [_period("R1", lvr_percent=70)],
            [_base(f"O{i}", f"Office {i}", sector="Office") for i in range(3)]
            + [_base("R1", "Retail One", sector="Retail")],
        )
        sectors = sector_ranks(cohort, METRICS_BY_KEY["lvr_percent"])
        assert "Office" in sectors
        assert "Retail" not in sectors, "a cohort of one cannot be ranked within"


class TestWorstQuartile:
    def test_counts_how_many_metrics_a_holding_trails_on(self):
        cohort = build_cohort(
            [_period("BAD", lvr_percent=70, wale_years=1.0, payout_ratio_percent=140),
             _period("OK", lvr_percent=35, wale_years=7.0, payout_ratio_percent=80),
             _period("MID", lvr_percent=45, wale_years=5.0, payout_ratio_percent=90),
             _period("GOOD", lvr_percent=30, wale_years=9.0, payout_ratio_percent=70)],
            [_base(s, s) for s in ("BAD", "OK", "MID", "GOOD")],
        )
        ordered = worst_quartile_count(cohort)
        assert ordered[0][0].syndicate_id == "BAD"
        assert ordered[0][1] == 3


class TestManagerScorecards:
    def _mixed(self):
        return build_cohort(
            [_period("C1", lvr_percent=50, icr_actual=2.0),
             _period("C2", lvr_percent=48, icr_actual=2.2),
             _period("O1", lvr_percent=30, icr_actual=3.0)],
            [_base("C1", "Cent One", manager="Centuria"),
             _base("C2", "Cent Two", manager="Centuria"),
             _base("O1", "Oyster One", manager="Oyster")],
        )

    def test_medians_across_a_managers_own_funds(self):
        summaries = {s.manager: s for s in manager_scorecards(self._mixed())}
        assert summaries["Centuria"].holdings == 2
        assert summaries["Centuria"].medians["lvr_percent"] == 49
        assert summaries["Oyster"].medians["lvr_percent"] == 30

    def test_disclosure_rate_is_a_property_of_the_manager(self):
        cohort = build_cohort(
            [_period("A", lvr_percent=40), _period("B", lvr_percent=45, icr_actual=2.0)],
            [_base("A", "A", manager="Quiet"), _base("B", "B", manager="Open")],
        )
        summaries = {s.manager: s for s in manager_scorecards(cohort)}
        assert summaries["Open"].disclosure_rate > summaries["Quiet"].disclosure_rate


class TestDisclosureGaps:
    def test_ranks_metrics_by_how_often_they_are_missing(self):
        cohort = build_cohort(
            [_period("A", lvr_percent=40), _period("B", lvr_percent=45), _period("C", lvr_percent=50)],
            [_base(s, s) for s in ("A", "B", "C")],
        )
        gaps = dict((m.key, missing) for m, missing, _ in disclosure_gaps(cohort))
        assert gaps["lvr_percent"] == 0
        assert gaps["icr_actual"] == 3


class TestScorecard:
    def test_one_syndicate_across_every_metric_it_discloses(self):
        cohort = build_cohort(
            [_period("A", lvr_percent=40, wale_years=5.0), _period("B", lvr_percent=60, wale_years=2.0)],
            [_base("A", "A"), _base("B", "B")],
        )
        card = dict((m.key, r) for m, r in scorecard(cohort, "A"))
        assert card["lvr_percent"].position == 1
        assert card["wale_years"].position == 1
        assert "icr_actual" not in card, "undisclosed metrics are absent, not ranked"
