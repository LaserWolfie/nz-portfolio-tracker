"""Tests for the delta engine.

Weighted deliberately toward false positives. A flag list that cries wolf gets
ignored wholesale, so "does not fire when it shouldn't" matters at least as much
as "fires when it should".
"""

import json
from datetime import date
from pathlib import Path

import pytest

from modules.deltas import (
    Severity,
    _num,
    evaluate,
    prior_period,
    review_all,
)
from modules.schema import SyndicateReport
from modules.storage import flatten_report

FIXTURE = Path(__file__).parent / "fixtures" / "augusta_fy2026_extracted.json"
AS_AT = date(2026, 9, 11)


def codes(flags):
    return {f.code for f in flags}


def by_code(flags, code):
    return next(f for f in flags if f.code == code)


@pytest.fixture
def augusta_row() -> dict:
    report = SyndicateReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return flatten_report(report, syndicate_id="SGB")


class TestBlankIsNotZero:
    """The single most dangerous confusion in this module."""

    @pytest.mark.parametrize("blank", ["", None, "  ", "N/A", "-", "none"])
    def test_blank_parses_to_none(self, blank):
        assert _num(blank) is None

    def test_real_zero_is_preserved(self):
        assert _num(0) == 0.0
        assert _num("0") == 0.0

    def test_formatted_numbers_parse(self):
        assert _num("$1,234.50") == 1234.50
        assert _num("46.52%") == 46.52

    def test_undisclosed_lvr_raises_no_flags(self):
        """An empty LVR cell must not read as an LVR of 0%."""
        current = {"lvr_percent": ""}
        prior = {"lvr_percent": 45.0}
        flags = evaluate(current, prior, {"lvr_covenant_threshold": 50.0}, AS_AT)
        assert "lvr_breach" not in codes(flags)
        assert "lvr_rising" not in codes(flags)

    def test_undisclosed_distribution_is_not_a_cut_to_zero(self):
        flags = evaluate({"distribution_rate": ""}, {"distribution_rate": 6.75}, None, AS_AT)
        assert "distribution_cut" not in codes(flags)


class TestFacilityExpiry:
    def test_refinanced_facility_does_not_cry_wolf(self):
        """Augusta's real case: matures 2026-09-30, refinanced out to 2029-06-23."""
        row = {
            "facility_expiry": "2026-09-30",
            "post_balance_date_facility_expiry": "2029-06-23",
            "effective_facility_expiry": "2029-06-23",
        }
        assert not (codes(evaluate(row, None, None, AS_AT)) & {
            "facility_expiry_urgent", "facility_expiry_near", "facility_expired"
        })

    def test_unrefinanced_facility_is_critical(self):
        row = {
            "facility_expiry": "2026-09-30",
            "post_balance_date_facility_expiry": "",
            "effective_facility_expiry": "2026-09-30",
        }
        flags = evaluate(row, None, None, AS_AT)
        assert by_code(flags, "facility_expiry_urgent").severity == Severity.CRITICAL

    def test_expiry_inside_eighteen_months_is_high(self):
        row = {"effective_facility_expiry": "2027-09-30"}
        assert by_code(evaluate(row, None, None, AS_AT),
                       "facility_expiry_near").severity == Severity.HIGH

    def test_distant_expiry_is_silent(self):
        row = {"effective_facility_expiry": "2030-01-01"}
        assert not codes(evaluate(row, None, None, AS_AT)) & {
            "facility_expiry_near", "facility_expiry_urgent"
        }

    def test_already_expired_is_critical(self):
        row = {"effective_facility_expiry": "2026-01-01"}
        assert by_code(evaluate(row, None, None, AS_AT),
                       "facility_expired").severity == Severity.CRITICAL


class TestICR:
    def test_covenant_without_actual_is_flagged(self):
        """Augusta discloses a 2.0x covenant but never the achieved ratio."""
        flags = evaluate({"icr_covenant": 2.0, "icr_actual": ""}, None, None, AS_AT)
        assert "icr_actual_not_disclosed" in codes(flags)
        assert "icr_breach" not in codes(flags)

    def test_breach_is_critical(self):
        flags = evaluate({"icr_covenant": 2.0, "icr_actual": 1.9}, None, None, AS_AT)
        assert by_code(flags, "icr_breach").severity == Severity.CRITICAL

    def test_thin_headroom_is_high(self):
        flags = evaluate({"icr_covenant": 2.0, "icr_actual": 2.2}, None, None, AS_AT)
        assert by_code(flags, "icr_headroom_thin").severity == Severity.HIGH

    def test_comfortable_headroom_is_silent(self):
        flags = evaluate({"icr_covenant": 2.0, "icr_actual": 3.5}, None, None, AS_AT)
        assert not codes(flags) & {"icr_breach", "icr_headroom_thin", "icr_headroom_watch"}

    def test_covenant_falls_back_to_baseline(self):
        flags = evaluate({"icr_actual": 1.5}, None, {"icr_covenant_threshold": 2.0}, AS_AT)
        assert "icr_breach" in codes(flags)

    def test_icr_is_never_divided_by_one_hundred(self):
        """Regression guard: clean_percent would turn 2.5 into 0.025."""
        flags = evaluate({"icr_covenant": 2.0, "icr_actual": 2.5}, None, None, AS_AT)
        assert "icr_breach" not in codes(flags)


class TestDistribution:
    def test_small_cut_is_medium(self):
        """6.75 -> 6.50 is a 3.7% decline, below the 10% material threshold."""
        flags = evaluate(
            {"distribution_rate": 6.50, "distribution_unit": "u"},
            {"distribution_rate": 6.75, "distribution_unit": "u"}, None, AS_AT)
        assert by_code(flags, "distribution_cut").severity == Severity.MEDIUM

    def test_large_cut_is_high(self):
        flags = evaluate(
            {"distribution_rate": 5.0, "distribution_unit": "u"},
            {"distribution_rate": 6.75, "distribution_unit": "u"}, None, AS_AT)
        assert by_code(flags, "distribution_cut").severity == Severity.HIGH

    def test_basis_change_is_not_reported_as_a_cut(self):
        """6.75% on subscription vs 7.43% on equity are not comparable."""
        flags = evaluate(
            {"distribution_rate": 6.75,
             "distribution_unit": "percent_per_annum_on_subscription_price"},
            {"distribution_rate": 7.43,
             "distribution_unit": "percent_per_annum_on_current_value"},
            None, AS_AT)
        assert "distribution_cut" not in codes(flags)
        assert "distribution_basis_changed" in codes(flags)

    def test_increase_is_silent(self):
        flags = evaluate(
            {"distribution_rate": 7.0, "distribution_unit": "u"},
            {"distribution_rate": 6.75, "distribution_unit": "u"}, None, AS_AT)
        assert "distribution_cut" not in codes(flags)

    def test_below_im_forecast(self):
        flags = evaluate({"distribution_rate": 6.0}, None,
                         {"im_forecast_distribution_rate": 7.0}, AS_AT)
        assert "below_im_forecast" in codes(flags)

    def test_payout_above_earnings(self):
        flags = evaluate({"payout_ratio_percent": 118}, None, None, AS_AT)
        assert by_code(flags, "payout_above_earnings").severity == Severity.HIGH

    def test_payout_below_one_hundred_is_silent(self):
        assert "payout_above_earnings" not in codes(
            evaluate({"payout_ratio_percent": 87}, None, None, AS_AT))


class TestTenancy:
    def test_vacancy_rise_flagged_when_occupancy_absent(self):
        """Augusta reports vacancy, never occupancy."""
        flags = evaluate({"vacancy_percent": 6.0, "occupancy_percent": ""},
                         {"vacancy_percent": 0.24, "occupancy_percent": ""}, None, AS_AT)
        assert by_code(flags, "vacancy_rise").severity == Severity.HIGH

    def test_occupancy_drop_flagged(self):
        flags = evaluate({"occupancy_percent": 90.0},
                         {"occupancy_percent": 98.0}, None, AS_AT)
        assert by_code(flags, "occupancy_drop").severity == Severity.HIGH

    def test_improvement_is_silent(self):
        flags = evaluate({"vacancy_percent": 0.1}, {"vacancy_percent": 5.0}, None, AS_AT)
        assert "vacancy_rise" not in codes(flags)


class TestDisclosureWithdrawn:
    def test_field_reported_then_absent_is_flagged(self):
        """The flag the required-and-nullable schema exists to enable."""
        flags = evaluate({"icr_actual": "", "valuation": 115},
                         {"icr_actual": 2.4, "valuation": 118}, None, AS_AT)
        assert "disclosure_withdrawn" in codes(flags)
        assert "icr actual" in by_code(flags, "disclosure_withdrawn").message

    def test_never_reported_is_not_withdrawn(self):
        flags = evaluate({"icr_actual": ""}, {"icr_actual": ""}, None, AS_AT)
        assert "disclosure_withdrawn" not in codes(flags)

    def test_no_prior_period_cannot_withdraw(self):
        assert "disclosure_withdrawn" not in codes(
            evaluate({"icr_actual": ""}, None, None, AS_AT))


class TestFeesVsDistributions:
    def test_fees_up_while_distributions_down(self):
        flags = evaluate(
            {"distribution_rate": 6.0, "distribution_unit": "u",
             "manager_fee_total_dollars": 1_200_000},
            {"distribution_rate": 6.75, "distribution_unit": "u",
             "manager_fee_total_dollars": 1_000_000}, None, AS_AT)
        assert by_code(flags, "fees_up_distributions_down").severity == Severity.HIGH

    def test_fees_up_while_distributions_also_up_is_silent(self):
        flags = evaluate(
            {"distribution_rate": 7.0, "distribution_unit": "u",
             "manager_fee_total_dollars": 1_200_000},
            {"distribution_rate": 6.75, "distribution_unit": "u",
             "manager_fee_total_dollars": 1_000_000}, None, AS_AT)
        assert "fees_up_distributions_down" not in codes(flags)

    def test_not_fired_across_a_basis_change(self):
        flags = evaluate(
            {"distribution_rate": 6.0, "distribution_unit": "a",
             "manager_fee_total_dollars": 1_200_000},
            {"distribution_rate": 6.75, "distribution_unit": "b",
             "manager_fee_total_dollars": 1_000_000}, None, AS_AT)
        assert "fees_up_distributions_down" not in codes(flags)


class TestExtractionQuality:
    def test_empty_extraction_is_critical(self):
        flags = evaluate({"completeness_found": 0, "completeness_total": 21}, None, None, AS_AT)
        assert by_code(flags, "extraction_empty").severity == Severity.CRITICAL

    def test_healthy_extraction_is_silent(self):
        flags = evaluate({"completeness_found": 18, "completeness_total": 21},
                         None, None, AS_AT)
        assert not codes(flags) & {"extraction_empty", "extraction_sparse"}


class TestAgainstTheRealReport:
    """The Augusta FY2026 row, with no prior period stored yet."""

    def test_no_false_facility_alarm(self, augusta_row):
        assert not codes(evaluate(augusta_row, None, None, AS_AT)) & {
            "facility_expiry_urgent", "facility_expired"
        }

    def test_swap_rolloff_is_caught(self, augusta_row):
        """Earliest swap expired 2026-06-05, before the 2026-09-11 review date."""
        assert "swap_expired" in codes(evaluate(augusta_row, None, None, AS_AT))

    def test_unverifiable_covenant_is_caught(self, augusta_row):
        assert "icr_actual_not_disclosed" in codes(evaluate(augusta_row, None, None, AS_AT))

    def test_healthy_payout_is_silent(self, augusta_row):
        assert "payout_above_earnings" not in codes(evaluate(augusta_row, None, None, AS_AT))


class TestSeverityDisplay:
    def test_severity_renders_as_a_name_not_a_number(self):
        """IntEnum would otherwise format as '3', which reaches the UI."""
        assert f"{Severity.HIGH}" == "HIGH"
        assert f"{Severity.HIGH:<8}" == "HIGH    "
        assert str(Severity.CRITICAL) == "CRITICAL"

    def test_severity_still_orders_numerically(self):
        assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM


class TestRanking:
    def test_flags_sort_most_serious_first(self):
        flags = evaluate(
            {"effective_facility_expiry": "2026-10-01", "payout_ratio_percent": 118},
            None, None, AS_AT)
        assert flags[0].severity == Severity.CRITICAL

    def test_prior_period_picks_the_most_recent_earlier_row(self):
        rows = [
            {"syndicate_id": "A", "period_end": "2024-03-31"},
            {"syndicate_id": "A", "period_end": "2025-03-31"},
            {"syndicate_id": "A", "period_end": "2026-03-31"},
            {"syndicate_id": "B", "period_end": "2025-03-31"},
        ]
        assert prior_period(rows, "A", "2026-03-31")["period_end"] == "2025-03-31"
        assert prior_period(rows, "A", "2024-03-31") is None

    def test_review_all_ranks_worst_syndicate_first(self):
        rows = [
            {"syndicate_id": "CALM", "period_end": "2026-03-31",
             "effective_facility_expiry": "2031-01-01"},
            {"syndicate_id": "RISK", "period_end": "2026-03-31",
             "effective_facility_expiry": "2026-10-01"},
        ]
        reviews = review_all(rows, [], AS_AT)
        assert reviews[0].syndicate_id == "RISK"
        assert reviews[0].needs_attention
        assert not reviews[1].needs_attention

    def test_review_all_uses_only_the_latest_period(self):
        rows = [
            {"syndicate_id": "A", "period_end": "2025-03-31", "payout_ratio_percent": 150},
            {"syndicate_id": "A", "period_end": "2026-03-31", "payout_ratio_percent": 87},
        ]
        reviews = review_all(rows, [], AS_AT)
        assert len(reviews) == 1
        assert reviews[0].period_end == "2026-03-31"
        assert "payout_above_earnings" not in codes(reviews[0].flags)


class TestPolicyRules:
    """The SIPO's own promises, tested against what the period row discloses.

    These exist so "the manager committed to X" is raised by the engine rather than
    remembered by a human reading 30 reports.
    """

    HEDGING = {"hedging_minimum_percent": 50}

    def test_hedged_share_below_the_minimum_fires(self):
        current = {"live_swap_notional": 20_000_000, "total_debt": 66_000_000}
        flags = evaluate(current, None, self.HEDGING, AS_AT)
        assert "hedging_below_policy" in codes(flags)
        flag = by_code(flags, "hedging_below_policy")
        assert flag.severity is Severity.HIGH
        assert "30% of debt hedged" in flag.message

    def test_hedged_share_above_the_minimum_is_silent(self):
        current = {"live_swap_notional": 40_000_000, "total_debt": 66_000_000}
        flags = evaluate(current, None, self.HEDGING, AS_AT)
        assert "hedging_below_policy" not in codes(flags)
        assert "hedging_policy_unverifiable" not in codes(flags)

    def test_every_swap_expired_is_a_real_zero_and_fires(self):
        """live_swap_notional 0.0 means the report listed swaps and all have expired."""
        current = {"live_swap_notional": 0.0, "total_debt": 66_000_000}
        flag = by_code(evaluate(current, None, self.HEDGING, AS_AT), "hedging_below_policy")
        assert "0% of debt hedged" in flag.message

    def test_notional_above_debt_is_unverifiable_not_compliance(self):
        """Augusta's real numbers: $66.9m of live notional against $53.5m of debt."""
        current = {"live_swap_notional": 66_875_000, "total_debt": 53_500_000}
        flags = evaluate(current, None, self.HEDGING, AS_AT)
        assert "hedging_below_policy" not in codes(flags)
        flag = by_code(flags, "hedging_policy_unverifiable")
        assert "staggered or forward-starting" in flag.message

    def test_undisclosed_notional_is_unverifiable_not_a_breach(self):
        """Most reports state a hedged percentage but no dollar notional."""
        current = {"total_debt": 66_000_000, "swap_count": 2}
        flags = evaluate(current, None, self.HEDGING, AS_AT)
        assert "hedging_below_policy" not in codes(flags)
        assert by_code(flags, "hedging_policy_unverifiable").severity is Severity.MEDIUM

    def test_no_swaps_mentioned_is_not_read_as_nothing_hedged(self):
        """`swap_count` is 0 both for "no swaps" and for "never mentioned"."""
        current = {"total_debt": 66_000_000, "swap_count": 0}
        assert "hedging_below_policy" not in codes(evaluate(current, None, self.HEDGING, AS_AT))

    def test_expired_swap_is_named_in_the_unverifiable_message(self):
        current = {"total_debt": 66_000_000, "earliest_swap_expiry": "2026-06-05"}
        flag = by_code(evaluate(current, None, self.HEDGING, AS_AT), "hedging_policy_unverifiable")
        assert "expired 05 Jun 2026" in flag.message

    def test_no_hedging_policy_raises_nothing(self):
        current = {"live_swap_notional": 1_000, "total_debt": 66_000_000}
        flags = evaluate(current, None, {"syndicate_id": "X"}, AS_AT)
        assert not {"hedging_below_policy", "hedging_policy_unverifiable"} & codes(flags)

    def test_occupancy_below_the_floor_fires(self):
        flags = evaluate({"occupancy_percent": 86.2}, None, {"occupancy_floor_percent": 90}, AS_AT)
        flag = by_code(flags, "occupancy_below_policy")
        assert flag.severity is Severity.HIGH
        assert "86.2% is below the SIPO's 90% floor" in flag.message

    def test_occupancy_at_the_floor_is_silent(self):
        flags = evaluate({"occupancy_percent": 90.0}, None, {"occupancy_floor_percent": 90}, AS_AT)
        assert "occupancy_below_policy" not in codes(flags)

    def test_undisclosed_occupancy_is_not_zero(self):
        flags = evaluate({"occupancy_percent": ""}, None, {"occupancy_floor_percent": 90}, AS_AT)
        assert "occupancy_below_policy" not in codes(flags)

    def test_nta_below_the_floor_fires(self):
        baseline = {"nta_floor_percent": 85, "formation_nav_per_unit": 50_000}
        flags = evaluate({"nta_per_unit": 40_000}, None, baseline, AS_AT)
        assert "nta_below_policy" in codes(flags)

    def test_nta_above_the_floor_is_silent(self):
        baseline = {"nta_floor_percent": 85, "formation_nav_per_unit": 50_000}
        flags = evaluate({"nta_per_unit": 45_431}, None, baseline, AS_AT)
        assert "nta_below_policy" not in codes(flags)

    def test_nta_rule_is_dormant_without_a_formation_nav(self):
        """original_investment_per_unit is NOT the formation NTA; substituting it
        would manufacture breaches, so the rule stays silent instead."""
        baseline = {"nta_floor_percent": 85, "original_investment_per_unit": 50_000}
        flags = evaluate({"nta_per_unit": 36_624}, None, baseline, AS_AT)
        assert "nta_below_policy" not in codes(flags)


class TestFiguresInThousands:
    """Centuria NZ Diversified stored a valuation of 153,980 for a $154m fund: the
    report's table was printed in thousands and read as dollars."""

    def test_a_tiny_valuation_is_flagged(self):
        current = {"valuation": 153_980, "total_debt": 60_475}
        flag = by_code(evaluate(current, None, None, AS_AT), "figures_may_be_in_thousands")
        assert flag.severity is Severity.HIGH
        assert "1,000x" in flag.message

    def test_a_real_valuation_is_silent(self):
        current = {"valuation": 137_000_000, "total_debt": 66_030_000}
        assert "figures_may_be_in_thousands" not in codes(evaluate(current, None, None, AS_AT))

    def test_an_undisclosed_valuation_is_not_flagged(self):
        """Blank is unknown, not a tiny number."""
        assert "figures_may_be_in_thousands" not in codes(evaluate({"valuation": ""}, None, None, AS_AT))
