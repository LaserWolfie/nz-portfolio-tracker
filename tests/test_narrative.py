"""Tests for narrative generation.

No API calls. What matters here is `build_facts()`: it decides what the writer
is permitted to see, so the "structured data only, never the PDF" guarantee is
asserted directly rather than left to prompt wording.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from modules.deltas import evaluate
from modules.narrative import (
    ManagerQuestion,
    SyndicateNarrative,
    build_facts,
    unsupported_numbers,
)
from modules.schema import SyndicateReport
from modules.storage import flatten_report

FIXTURE = Path(__file__).parent / "fixtures" / "augusta_fy2026_extracted.json"
AS_AT = date(2026, 9, 11)


@pytest.fixture
def augusta_row() -> dict:
    report = SyndicateReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return flatten_report(report, syndicate_id="SGB")


@pytest.fixture
def augusta_facts(augusta_row) -> dict:
    return build_facts(augusta_row, evaluate(augusta_row, None, None, AS_AT))


class TestTheDocumentStaysOut:
    """The PDF is read once, by the extractor. It must not reach the writer."""

    def test_raw_json_is_never_included(self, augusta_row, augusta_facts):
        assert "raw_json" in augusta_row, "the row really does carry it"
        assert "raw_json" not in json.dumps(augusta_facts)

    def test_verbatim_quotes_are_never_included(self, augusta_row, augusta_facts):
        """source_text is prose lifted straight from the document."""
        blob = json.dumps(augusta_facts)
        assert "source_text" not in blob
        quote = "interest cover ratio requires"
        assert quote not in blob.lower()

    def test_page_numbers_are_not_smuggled_in(self, augusta_facts):
        assert not [k for k in json.dumps(augusta_facts).split('"') if k.endswith("_page")]

    def test_bookkeeping_columns_are_excluded(self, augusta_facts):
        blob = json.dumps(augusta_facts)
        for column in ("source_filename", "extracted_at", "completeness_found"):
            assert column not in blob


class TestFactsContent:
    def test_disclosed_figures_are_present(self, augusta_facts):
        current = augusta_facts["current_period"]
        assert current["valuation"] == 115_000_000
        assert current["lvr_percent"] == 46.52
        assert current["capitalisation_rate_percent"] == 6.38

    def test_undisclosed_figures_are_listed_not_zeroed(self, augusta_facts):
        """A null is a fact about the manager, not a zero."""
        assert "icr_actual" not in augusta_facts["current_period"]
        assert "icr_actual" in augusta_facts["not_disclosed"]

    def test_flags_are_passed_through_with_severity(self, augusta_facts):
        codes = {f["code"] for f in augusta_facts["flags"]}
        assert "icr_actual_not_disclosed" in codes
        assert all(f["severity"] in
                   {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"} for f in augusta_facts["flags"])

    def test_prior_period_is_included_when_given(self, augusta_row):
        prior = dict(augusta_row)
        prior["period_end"] = "2025-03-31"
        prior["valuation"] = 118_750_000
        facts = build_facts(augusta_row, [], prior=prior)
        assert facts["prior_period"]["period_end"] == "2025-03-31"
        assert facts["prior_period"]["valuation"] == 118_750_000

    def test_baseline_is_included_when_given(self, augusta_row):
        facts = build_facts(augusta_row, [], baseline={
            "canonical_name": "Augusta St Georges Bay Road Property Trust",
            "im_forecast_distribution_rate": 7.0,
            "syndicate_id": "SGB",
        })
        block = facts["baseline_from_information_memorandum"]
        assert block["im_forecast_distribution_rate"] == 7.0
        assert "syndicate_id" not in block, "only the listed baseline columns"

    def test_blank_cells_do_not_become_zero(self):
        facts = build_facts({"syndicate_id": "X", "period_end": "2026-03-31",
                             "lvr_percent": "", "valuation": 0}, [])
        assert "lvr_percent" not in facts["current_period"]
        assert facts["current_period"]["valuation"] == 0, "a real zero survives"

    def test_extraction_caveats_are_labelled_separately(self, augusta_row):
        facts = build_facts(augusta_row, [])
        assert "data_caveats_from_extraction" in facts
        assert facts["data_caveats_from_extraction"] not in facts["current_period"].values()


class TestUnsupportedNumberAudit:
    def _narrative(self, note):
        return SyndicateNarrative(headline="x", note=note, questions=[], data_gaps=[])

    def test_quoted_figure_is_supported(self, augusta_facts):
        n = self._narrative("LVR stands at 46.52% against a valuation of 115000000.")
        assert unsupported_numbers(n, augusta_facts) == []

    def test_invented_figure_is_caught(self, augusta_facts):
        n = self._narrative("Gearing rose to 61.7% during the period.")
        assert "61.7" in unsupported_numbers(n, augusta_facts)

    def test_trailing_zeros_do_not_trip_the_audit(self, augusta_facts):
        n = self._narrative("The cap rate is 6.380%.")
        assert unsupported_numbers(n, augusta_facts) == []

    def test_thousands_separators_do_not_trip_the_audit(self, augusta_facts):
        """Splitting '$115,000,000' on commas made every large figure look invented."""
        n = self._narrative(
            "Valuation of $115,000,000 against total debt of $53,500,000."
        )
        assert unsupported_numbers(n, augusta_facts) == []

    def test_questions_are_audited_too(self, augusta_facts):
        n = SyndicateNarrative(
            headline="x", note="y", data_gaps=[],
            questions=[ManagerQuestion(question="Why did ICR fall to 1.42x?", basis="none")],
        )
        assert "1.42" in unsupported_numbers(n, augusta_facts)
