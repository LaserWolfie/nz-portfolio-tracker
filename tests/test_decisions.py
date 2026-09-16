"""Tests for the decision-window register and the refinance scenario.

Weighted toward the refusals, like the rest of this codebase: a missing figure must
never be modelled as a zero, and a hedge that cannot be measured must not be reported
as full cover.
"""

from datetime import date

import pytest

from modules.decisions import (
    FACILITY_EXPIRY,
    SWAP_EXPIRY,
    TERM_EXPIRY,
    income_at_risk,
    refinance_sensitivity,
    sensitivity_all,
    windows,
)

AS_AT = date(2026, 9, 16)

BASELINE = [
    {"syndicate_id": "GRENFELL", "canonical_name": "Centuria Grenfell St",
     "fund_term_expiry": "2027-04-01"},
    {"syndicate_id": "PASTORAL", "canonical_name": "Pastoral House"},
]


def _period(**over):
    row = {"syndicate_id": "GRENFELL", "period_end": "2026-06-30"}
    row.update(over)
    return row


class TestDecisionWindows:
    def test_the_three_kinds_are_collected(self):
        row = _period(effective_facility_expiry="2027-03-31", earliest_swap_expiry="2027-03-15")
        kinds = {w.kind for w in windows([row], BASELINE, AS_AT)}
        assert kinds == {TERM_EXPIRY, FACILITY_EXPIRY, SWAP_EXPIRY}

    def test_soonest_first(self):
        row = _period(effective_facility_expiry="2027-03-31", earliest_swap_expiry="2027-03-15")
        got = [w.when for w in windows([row], BASELINE, AS_AT)]
        assert got == sorted(got)

    def test_a_passed_date_is_kept_and_marked(self):
        """A facility that matured last month is a live problem, not history."""
        row = _period(syndicate_id="PASTORAL", effective_facility_expiry="2026-07-18")
        window = windows([row], BASELINE, AS_AT)[0]
        assert window.is_past
        assert "PASSED" in str(window)

    def test_the_horizon_hides_distant_dates_but_not_past_ones(self):
        rows = [_period(effective_facility_expiry="2031-01-01"),
                _period(syndicate_id="PASTORAL", earliest_swap_expiry="2026-07-18")]
        got = windows(rows, BASELINE, AS_AT, horizon_months=12)
        assert date(2031, 1, 1) not in [w.when for w in got], "distant dates are hidden"
        assert date(2026, 7, 18) in [w.when for w in got], "a passed date is never hidden"
        # Grenfell's term expiry (Apr 2027) is inside the horizon and stays.
        assert date(2027, 4, 1) in [w.when for w in got]

    def test_a_syndicate_without_dates_produces_nothing(self):
        assert windows([_period(syndicate_id="PASTORAL")], BASELINE, AS_AT) == []

    def test_only_the_latest_period_is_read(self):
        rows = [_period(period_end="2025-06-30", effective_facility_expiry="2026-01-01"),
                _period(period_end="2026-06-30", effective_facility_expiry="2028-01-01")]
        got = [w for w in windows(rows, BASELINE, AS_AT) if w.kind == FACILITY_EXPIRY]
        assert [w.when for w in got] == [date(2028, 1, 1)]

    def test_the_term_date_comes_from_the_baseline(self):
        """A scheme's term is constitutional, not a property of one reporting period."""
        window = next(w for w in windows([_period()], BASELINE, AS_AT) if w.kind == TERM_EXPIRY)
        assert window.when == date(2027, 4, 1)
        assert window.source_field == "fund_term_expiry"


class TestRefinanceSensitivity:
    FULL = {"syndicate_id": "X", "period_end": "2026-03-31", "report_type": "Annual Report",
            "total_debt": 10_000_000, "adjusted_operating_profit": 1_000_000,
            "payout_ratio_percent": 90.0}

    def test_the_cost_is_debt_times_the_shock(self):
        scenario = refinance_sensitivity(self.FULL, None, shock_points=2.0)
        assert scenario.extra_interest == pytest.approx(200_000)
        assert scenario.profit_consumed_percent == pytest.approx(20.0)

    def test_cover_falls_and_the_shortfall_is_reported(self):
        scenario = refinance_sensitivity(self.FULL, None, shock_points=2.0)
        assert scenario.cover_before == pytest.approx(1 / 0.9)
        assert scenario.cover_after == pytest.approx(800_000 / 900_000)
        assert scenario.distribution_uncovered_after is True

    def test_a_syndicate_that_survives_the_shock_is_not_flagged(self):
        row = dict(self.FULL, adjusted_operating_profit=3_000_000, payout_ratio_percent=50.0)
        scenario = refinance_sensitivity(row, None, shock_points=2.0)
        assert scenario.distribution_uncovered_after is False

    def test_undisclosed_debt_is_not_modelled_as_no_debt(self):
        scenario = refinance_sensitivity(dict(self.FULL, total_debt=""), None)
        assert scenario.is_computable is False
        assert scenario.extra_interest is None
        assert "not disclosed" in scenario.note

    def test_undisclosed_profit_leaves_cover_unknown_not_zero(self):
        scenario = refinance_sensitivity(dict(self.FULL, adjusted_operating_profit=""), None)
        assert scenario.extra_interest == pytest.approx(200_000)
        assert scenario.cover_after is None
        assert scenario.distribution_uncovered_after is None

    def test_staggered_swaps_are_noted_and_the_whole_facility_is_exposed(self):
        """Live notional above the debt means the hedged share is not derivable."""
        row = dict(self.FULL, live_swap_notional=12_500_000)
        scenario = refinance_sensitivity(row, None, shock_points=2.0)
        assert scenario.exposed_debt == 10_000_000, "no credit taken for an unmeasurable hedge"
        assert "staggered" in scenario.note

    def test_the_shock_size_is_a_parameter(self):
        assert refinance_sensitivity(self.FULL, None, 1.0).extra_interest == pytest.approx(100_000)
        assert refinance_sensitivity(self.FULL, None, 3.5).extra_interest == pytest.approx(350_000)

    def test_worst_cover_is_ranked_first_and_unknowns_go_last(self):
        rows = [
            dict(self.FULL, syndicate_id="SAFE", adjusted_operating_profit=5_000_000,
                 payout_ratio_percent=40.0),
            dict(self.FULL, syndicate_id="TIGHT"),
            dict(self.FULL, syndicate_id="UNKNOWN", adjusted_operating_profit=""),
        ]
        assert [s.syndicate_id for s in sensitivity_all(rows)] == ["TIGHT", "SAFE", "UNKNOWN"]


class TestIncomeAtRisk:
    PERIODS = [
        {"syndicate_id": "TIGHT", "period_end": "2026-03-31", "report_type": "Annual Report",
         "total_debt": 10_000_000, "adjusted_operating_profit": 1_000_000,
         "payout_ratio_percent": 90.0},
        {"syndicate_id": "SAFE", "period_end": "2026-03-31", "report_type": "Annual Report",
         "total_debt": 1_000_000, "adjusted_operating_profit": 5_000_000,
         "payout_ratio_percent": 40.0},
    ]
    BASE = [{"syndicate_id": "TIGHT", "canonical_name": "Tight Fund"},
            {"syndicate_id": "SAFE", "canonical_name": "Safe Fund"}]
    HOLDINGS = [
        {"Entity_Name": "Tight Fund", "Owner_Entity": "Roy Wilson", "Annual_Distribution": "$9,000.00"},
        {"Entity_Name": "Safe Fund", "Owner_Entity": "Cambridge", "Annual_Distribution": "$1,000.00"},
        {"Entity_Name": "Unknown Fund", "Owner_Entity": "Cambridge", "Annual_Distribution": "$500.00"},
    ]

    def _resolve(self, name, baseline_rows):
        for row in baseline_rows:
            if row["canonical_name"].lower() == name.strip().lower():
                return row["syndicate_id"]
        return None

    def test_income_behind_an_uncovered_cover_ratio_is_totalled(self):
        got = income_at_risk(self.PERIODS, self.BASE, self.HOLDINGS, self._resolve, 2.0)
        assert got.total_income == pytest.approx(10_500)
        assert got.income_uncovered == pytest.approx(9_000)
        assert got.uncovered_share == pytest.approx(9_000 / 10_500 * 100)

    def test_an_unmatched_holding_is_unknown_never_safe(self):
        got = income_at_risk(self.PERIODS, self.BASE, self.HOLDINGS, self._resolve, 2.0)
        assert got.income_unknown == pytest.approx(500)

    def test_the_flagged_holdings_name_their_owner(self):
        got = income_at_risk(self.PERIODS, self.BASE, self.HOLDINGS, self._resolve, 2.0)
        assert got.holdings_uncovered == [("TIGHT", "Roy Wilson", 9_000.0)]


class TestReportingPeriod:
    """A quarter's earnings against a year of interest overstates the damage fourfold."""

    QUARTERLY = {"syndicate_id": "Q", "period_end": "2026-03-31", "report_type": "Quarterly Report",
                 "total_debt": 10_000_000, "adjusted_operating_profit": 250_000,
                 "payout_ratio_percent": 80.0}

    def test_a_quarterly_profit_is_annualised(self):
        scenario = refinance_sensitivity(self.QUARTERLY, None, 2.0)
        assert scenario.operating_profit == pytest.approx(1_000_000), "250k a quarter is 1m a year"
        assert "annualised" in scenario.note

    def test_an_annual_report_is_untouched(self):
        row = dict(self.QUARTERLY, report_type="2026 Annual Report")
        assert refinance_sensitivity(row, None, 2.0).operating_profit == pytest.approx(250_000)

    def test_a_half_year_doubles(self):
        row = dict(self.QUARTERLY, report_type="Biannual report")
        assert refinance_sensitivity(row, None, 2.0).operating_profit == pytest.approx(500_000)

    def test_an_unlabelled_period_leaves_cover_unknown(self):
        """Assuming annual would silently flatter a quarterly reporter."""
        row = dict(self.QUARTERLY, report_type="Fund update")
        scenario = refinance_sensitivity(row, None, 2.0)
        assert scenario.cover_after is None
        assert "does not say what period" in scenario.note
