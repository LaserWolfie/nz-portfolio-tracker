"""Contract tests for the extraction schema.

These make no API calls. They guard the properties the delta engine relies on:
every field present on every run, nulls explicit, units unscaled.
"""

import pytest
from pydantic import ValidationError

from modules.extraction import completeness
from modules.schema import Figure, SyndicateReport


def _null_figure():
    return {"value": None, "page": None, "source_text": None}


def _figure(value, page=1, quote="quoted"):
    return {"value": value, "page": page, "source_text": quote}


def _minimal_payload(**overrides):
    """A fully-null report -- the shape the model must return for an unreadable PDF."""
    payload = {
        "entity_name": _null_figure(),
        "manager_name": _null_figure(),
        "period_end_date": _null_figure(),
        "report_type": _null_figure(),
        "valuation": _null_figure(),
        "valuation_date": _null_figure(),
        "nta_per_unit": _null_figure(),
        "cash": _null_figure(),
        "total_debt": _null_figure(),
        "facility_expiry": _null_figure(),
        "lvr_percent": _null_figure(),
        "icr_actual": _null_figure(),
        "icr_covenant": _null_figure(),
        "swap_expiries": [],
        "occupancy_percent": _null_figure(),
        "wale_years": _null_figure(),
        "distribution_rate": _null_figure(),
        "distribution_unit": "unknown",
        "payout_ratio_percent": _null_figure(),
        "adjusted_operating_profit": _null_figure(),
        "adjusted_operating_profit_forecast": _null_figure(),
        "manager_fees": [],
        "extraction_notes": None,
    }
    payload.update(overrides)
    return payload


class TestSchemaContract:
    def test_every_field_is_required_in_json_schema(self):
        """No field may be optional, so the model cannot silently omit one."""
        schema = SyndicateReport.model_json_schema()
        assert sorted(schema["properties"]) == sorted(schema["required"])

    def test_every_figure_field_accepts_null(self):
        """'Not disclosed' must be expressible for every figure."""
        report = SyndicateReport.model_validate(_minimal_payload())
        assert report.valuation.value is None
        assert report.icr_covenant.value is None

    def test_omitting_a_field_is_rejected(self):
        """An omitted key is an error, not an implicit null."""
        payload = _minimal_payload()
        del payload["lvr_percent"]
        with pytest.raises(ValidationError):
            SyndicateReport.model_validate(payload)

    def test_zero_is_distinct_from_null(self):
        """Zero is a reported value; null is absence. They must not collapse."""
        report = SyndicateReport.model_validate(
            _minimal_payload(cash=_figure(0.0))
        )
        assert report.cash.value == 0.0
        assert report.cash.value is not None


class TestUnits:
    def test_icr_stays_a_multiple(self):
        """Regression guard for the clean_percent trap: 2.5x must stay 2.5."""
        report = SyndicateReport.model_validate(
            _minimal_payload(icr_actual=_figure(2.5), icr_covenant=_figure(1.75))
        )
        assert report.icr_actual.value == 2.5
        assert report.icr_covenant.value == 1.75

    def test_wale_stays_in_years(self):
        report = SyndicateReport.model_validate(_minimal_payload(wale_years=_figure(4.2)))
        assert report.wale_years.value == 4.2

    def test_percentages_are_whole_numbers(self):
        report = SyndicateReport.model_validate(
            _minimal_payload(lvr_percent=_figure(42.5), occupancy_percent=_figure(97.5))
        )
        assert report.lvr_percent.value == 42.5
        assert report.occupancy_percent.value == 97.5


class TestProvenance:
    def test_figure_carries_page_and_quote(self):
        figure = Figure.model_validate(_figure(1_250_000.0, page=7, quote="valued at $1,250,000"))
        assert figure.page == 7
        assert "1,250,000" in figure.source_text


class TestCompleteness:
    def test_empty_report_scores_zero(self):
        report = SyndicateReport.model_validate(_minimal_payload())
        found, total = completeness(report)
        assert found == 0
        assert total > 0

    def test_populated_fields_are_counted(self):
        report = SyndicateReport.model_validate(
            _minimal_payload(valuation=_figure(1.0), cash=_figure(2.0))
        )
        found, _ = completeness(report)
        assert found == 2
