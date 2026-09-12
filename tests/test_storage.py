"""Tests for per-period storage.

No network. The Google Sheets worksheet is faked; what is being tested is that
rows land under the right headers and that re-scanning a document does not
create a second history for the same quarter.

`augusta_fy2026_extracted.json` is genuine model output from the FY2026 annual
report, so flattening is exercised against real data rather than a mock.
"""

import json
from pathlib import Path

import pytest

from modules.schema import SyndicateReport
from modules.storage import (
    StorageError,
    flatten_report,
    period_columns,
    resolve_syndicate_id,
    row_from_dict,
    upsert_period,
)

FIXTURE = Path(__file__).parent / "fixtures" / "augusta_fy2026_extracted.json"


@pytest.fixture
def augusta() -> SyndicateReport:
    return SyndicateReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


class FakeWorksheet:
    """Just enough gspread surface for the storage helpers."""

    def __init__(self, header, rows=None):
        self.rows = [list(header)] + [list(r) for r in (rows or [])]

    def row_values(self, index):
        return list(self.rows[index - 1])

    def col_values(self, index):
        return [r[index - 1] if index - 1 < len(r) else "" for r in self.rows]

    def append_row(self, values, value_input_option=None):
        self.rows.append(list(values))

    def update(self, range_name, values):
        line = int("".join(c for c in range_name if c.isdigit()))
        self.rows[line - 1] = list(values[0])


class TestHeaderNameWriting:
    """The regression this whole module exists to prevent."""

    def test_values_follow_header_order_not_dict_order(self):
        header = ["b", "a", "c"]
        row = row_from_dict(header, {"a": 1, "b": 2, "c": 3})
        assert row == [2, 1, 3]

    def test_reordered_sheet_still_writes_correctly(self):
        """Inserting a column used to shift every value one field left."""
        values = {"syndicate_id": "SGB", "lvr_percent": 46.52, "valuation": 115_000_000}

        original = row_from_dict(["syndicate_id", "lvr_percent", "valuation"], values)
        reordered = row_from_dict(
            ["syndicate_id", "notes_someone_added", "valuation", "lvr_percent"], values
        )

        assert original == ["SGB", 46.52, 115_000_000]
        assert reordered == ["SGB", "", 115_000_000, 46.52]

    def test_unknown_column_raises_rather_than_dropping_data(self):
        with pytest.raises(StorageError, match="no column for"):
            row_from_dict(["a", "b"], {"a": 1, "c": 3})

    def test_missing_value_is_blank_not_zero(self):
        """A null figure must not read back as a real zero."""
        row = row_from_dict(["a", "b"], {"a": None})
        assert row == ["", ""]
        assert 0 not in row


class TestFlatten:
    def test_scalar_figures_carry_value_and_page(self, augusta):
        row = flatten_report(augusta, syndicate_id="SGB")
        assert row["valuation"] == 115_000_000
        assert row["lvr_percent"] == 46.52
        assert row["wale_years"] == 3.35
        assert row["valuation_page"] is not None

    def test_undisclosed_figure_stays_none(self, augusta):
        row = flatten_report(augusta, syndicate_id="SGB")
        assert row["icr_actual"] is None
        assert row["occupancy_percent"] is None

    def test_effective_expiry_prefers_the_refinance(self, augusta):
        """Balance-date expiry is 2026-09-30; refinanced out to 2029-06-23."""
        row = flatten_report(augusta, syndicate_id="SGB")
        assert row["facility_expiry"] == "2026-09-30"
        assert row["post_balance_date_facility_expiry"] == "2029-06-23"
        assert row["effective_facility_expiry"] == "2029-06-23"

    def test_swaps_are_summarised_and_preserved(self, augusta):
        row = flatten_report(augusta, syndicate_id="SGB")
        assert row["swap_count"] == 4
        assert row["earliest_swap_expiry"] == "2026-06-05"
        assert row["total_swap_notional"] == pytest.approx(66_875_000)
        assert len(json.loads(row["raw_json"])["debt"]["swap_expiries"]) == 4

    def test_fees_total_both_bases(self, augusta):
        row = flatten_report(augusta, syndicate_id="SGB")
        assert row["manager_fee_total_dollars"] > 0
        assert row["manager_fee_total_percent"] > 0

    def test_every_key_has_a_column(self, augusta):
        """Flatten and the header must not drift apart."""
        row = flatten_report(augusta, syndicate_id="SGB")
        assert not set(row) - set(period_columns())


class TestSyndicateMatching:
    BASELINE = [
        {
            "syndicate_id": "SGB",
            "canonical_name": "Augusta St Georges Bay Road Property Trust",
            "aliases": "St Georges Bay Road|96 St Georges Bay Road",
        },
        {
            "syndicate_id": "BWY",
            "canonical_name": "33 Broadway Trust",
            "aliases": "33 Broadway",
        },
    ]

    def test_exact_canonical_name(self):
        assert resolve_syndicate_id(
            "Augusta St Georges Bay Road Property Trust", self.BASELINE
        ) == "SGB"

    def test_alias(self):
        assert resolve_syndicate_id("96 St Georges Bay Road", self.BASELINE) == "SGB"

    def test_legal_suffix_and_case_are_ignored(self):
        assert resolve_syndicate_id("33 BROADWAY TRUST, LIMITED", self.BASELINE) == "BWY"

    def test_partial_match_against_an_alias(self):
        """A holding recorded under its tenant keeps that name as an alias, so a
        document naming only part of it must still resolve."""
        baseline = [{
            "syndicate_id": "CENT-AIRWAYSSOE",
            "canonical_name": "Sir William Pickering Drive Limited Partnership",
            "aliases": "AIRWAYS soe|Sir William Pickering Drive",
        }]
        assert resolve_syndicate_id("Airways", baseline) == "CENT-AIRWAYSSOE"

    def test_ambiguous_partial_match_still_returns_none(self):
        """Two syndicates could be meant, so guessing is worse than asking."""
        baseline = [
            {"syndicate_id": "A", "canonical_name": "Graham Street A", "aliases": "Graham"},
            {"syndicate_id": "B", "canonical_name": "Graham Street B", "aliases": "Graham"},
        ]
        assert resolve_syndicate_id("Graham", baseline) is None

    def test_unknown_name_returns_none(self):
        """Better to ask than to file a report against the wrong syndicate."""
        assert resolve_syndicate_id("Some Other Property Fund", self.BASELINE) is None

    def test_empty_name_returns_none(self):
        assert resolve_syndicate_id("", self.BASELINE) is None


class TestUpsert:
    def _sheet(self):
        return FakeWorksheet(["syndicate_id", "period_end", "valuation"])

    def test_first_write_appends(self):
        sheet = self._sheet()
        result = upsert_period(
            sheet, {"syndicate_id": "SGB", "period_end": "2026-03-31", "valuation": 115}
        )
        assert result == "appended"
        assert len(sheet.rows) == 2

    def test_rescanning_the_same_period_replaces(self):
        """Re-running a document must not create two histories for one quarter."""
        sheet = self._sheet()
        upsert_period(
            sheet, {"syndicate_id": "SGB", "period_end": "2026-03-31", "valuation": 115}
        )
        result = upsert_period(
            sheet, {"syndicate_id": "SGB", "period_end": "2026-03-31", "valuation": 999}
        )
        assert result == "updated row 2"
        assert len(sheet.rows) == 2
        assert sheet.rows[1][2] == 999

    def test_a_new_period_appends_alongside(self):
        sheet = self._sheet()
        upsert_period(
            sheet, {"syndicate_id": "SGB", "period_end": "2026-03-31", "valuation": 115}
        )
        upsert_period(
            sheet, {"syndicate_id": "SGB", "period_end": "2027-03-31", "valuation": 120}
        )
        assert len(sheet.rows) == 3

    def test_period_row_requires_identity(self):
        with pytest.raises(StorageError, match="syndicate_id and period_end"):
            upsert_period(self._sheet(), {"valuation": 115})
