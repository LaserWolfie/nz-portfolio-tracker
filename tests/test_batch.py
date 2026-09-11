"""Tests for batch intake.

No API calls: extraction is faked, so the wiring, matching and error isolation
can be exercised without spending money.
"""

import json
from pathlib import Path

import pytest

from modules import batch
from modules.batch import BatchItem, Status, name_from_filename, plan_batch
from modules.extraction import ExtractionError
from modules.schema import SyndicateReport

FIXTURE = Path(__file__).parent / "fixtures" / "augusta_fy2026_extracted.json"

BASELINE = [
    {"syndicate_id": "SGB",
     "canonical_name": "Augusta St Georges Bay Road Property Trust",
     "aliases": "St Georges Bay Road|96 St Georges Bay Road"},
    {"syndicate_id": "CENT-BUILDINGA",
     "canonical_name": "Building A Graham Street Limited Partnership", "aliases": ""},
    {"syndicate_id": "CENT-BUILDINGB",
     "canonical_name": "Building B Graham Street Limited Partnership", "aliases": ""},
    {"syndicate_id": "CENT-GOVT1", "canonical_name": "Centuria Govt Income 1", "aliases": ""},
    {"syndicate_id": "CENT-GOVT2", "canonical_name": "Centuria Govt Income 2", "aliases": ""},
]


@pytest.fixture
def augusta() -> SyndicateReport:
    return SyndicateReport.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


class TestFilenameReading:
    def test_report_words_and_years_are_stripped(self):
        assert name_from_filename(
            "Augusta_St_Georges_Bay_Road_Property_Trust_Annual_Report_June_2026.pdf"
        ) == "Augusta St Georges Bay Road Property Trust"

    def test_meaningful_numbers_survive(self):
        """Stripping every digit made Govt Income 1 and 2 indistinguishable."""
        assert "1" in name_from_filename("Centuria-Govt-Income-1-Annual-Report-2026.pdf")
        assert "33" in name_from_filename("33-Broadway-Valuation-Update-April-2025.pdf")

    def test_a_path_is_reduced_to_its_basename(self):
        assert name_from_filename(r"C:\reports\PMG Generation Fund.pdf") == "PMG Generation Fund"


class TestPlanning:
    def test_matches_without_calling_the_api(self):
        items = plan_batch(["Centuria Govt Income 1 Annual Report 2026.pdf"], BASELINE)
        assert items[0].status == Status.MATCHED
        assert items[0].syndicate_id == "CENT-GOVT1"

    def test_near_identical_syndicates_stay_apart(self):
        items = plan_batch([
            "Building A Graham Street LP Annual Report 2026.pdf",
            "Building B Graham Street LP Annual Report 2026.pdf",
        ], BASELINE)
        assert [i.syndicate_id for i in items] == ["CENT-BUILDINGA", "CENT-BUILDINGB"]

    def test_unrecognised_file_is_flagged_not_guessed(self):
        items = plan_batch(["scan0012.pdf"], BASELINE)
        assert items[0].status == Status.UNMATCHED
        assert items[0].syndicate_id is None

    def test_cost_is_estimated_before_spending(self):
        items = plan_batch(["a.pdf", "b.pdf"], BASELINE)
        assert batch.estimated_cost(items) == pytest.approx(
            2 * batch.ESTIMATED_COST_PER_DOCUMENT)


class TestExtractionOutcomes:
    def _extract(self, monkeypatch, item, result):
        def fake(**kwargs):
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(batch, "extract_report", fake)
        return batch.extract_one(item, b"%PDF", BASELINE)

    def test_successful_extraction_records_identity(self, monkeypatch, augusta):
        item = self._extract(monkeypatch, BatchItem("augusta.pdf"), augusta)
        assert item.status == Status.EXTRACTED
        assert item.syndicate_id == "SGB"
        assert item.period_end == "2026-03-31"
        assert item.report is not None

    def test_extraction_failure_is_isolated(self, monkeypatch):
        item = self._extract(monkeypatch, BatchItem("bad.pdf"),
                             ExtractionError("image-only PDF"))
        assert item.status == Status.FAILED
        assert "image-only" in item.error
        assert item.report is None

    def test_unexpected_error_is_also_caught(self, monkeypatch):
        """One malformed file must not take the other twenty-nine with it."""
        item = self._extract(monkeypatch, BatchItem("weird.pdf"), RuntimeError("boom"))
        assert item.status == Status.FAILED
        assert "boom" in item.error

    def test_document_name_overrides_a_wrong_filename(self, monkeypatch, augusta):
        """A file named for the wrong syndicate must not corrupt that syndicate."""
        item = BatchItem("Centuria Govt Income 1 Annual Report.pdf")
        item.filename_match = "CENT-GOVT1"
        item = self._extract(monkeypatch, item, augusta)
        assert item.status == Status.CONFLICT
        assert item.syndicate_id == "SGB", "the document, not the filename, decides"
        assert item.needs_attention

    def test_agreement_is_not_a_conflict(self, monkeypatch, augusta):
        item = BatchItem("augusta.pdf")
        item.filename_match = "SGB"
        item = self._extract(monkeypatch, item, augusta)
        assert item.status == Status.EXTRACTED
        assert not item.needs_attention


class TestSparseDocuments:
    """A real tax statement extracted 4 of 32 figures for a syndicate and period
    that a genuine report also covers."""

    def _thin_report(self, augusta):
        thin = augusta.model_copy(deep=True)
        for group in ("valuation", "debt", "tenancy", "returns", "conduct"):
            obj = getattr(thin, group)
            for name in obj.__class__.model_fields:
                figure = getattr(obj, name)
                if hasattr(figure, "value") and not isinstance(figure, list):
                    try:
                        figure.value = None
                    except (AttributeError, ValueError):
                        pass
        return thin

    def test_thin_document_is_marked_sparse(self, monkeypatch, augusta):
        monkeypatch.setattr(batch, "extract_report", lambda **kw: self._thin_report(augusta))
        item = batch.extract_one(BatchItem("tax_statement.pdf"), b"%PDF", BASELINE)
        assert item.status == Status.SPARSE
        assert item.needs_attention

    def test_sparse_is_not_saved_by_default(self, monkeypatch, augusta):
        saved = []
        monkeypatch.setattr(batch, "save_report", lambda *a, **kw: saved.append(1) or "ok")
        item = BatchItem("tax.pdf", status=Status.SPARSE, syndicate_id="SGB",
                         period_end="2026-03-31", report=augusta)
        batch.save_batch([item])
        assert saved == [], "a tax statement must not overwrite a real period row"

    def test_sparse_saves_when_explicitly_included(self, monkeypatch, augusta):
        monkeypatch.setattr(batch, "save_report", lambda *a, **kw: "appended")
        item = BatchItem("tax.pdf", status=Status.SPARSE, syndicate_id="SGB",
                         period_end="2026-03-31", report=augusta)
        batch.save_batch([item], include_sparse=True)
        assert item.status == Status.SAVED

    def test_a_full_report_is_not_sparse(self, monkeypatch, augusta):
        monkeypatch.setattr(batch, "extract_report", lambda **kw: augusta)
        item = batch.extract_one(BatchItem("augusta.pdf"), b"%PDF", BASELINE)
        assert item.status == Status.EXTRACTED


class TestBatchRun:
    def test_one_failure_does_not_stop_the_others(self, monkeypatch, augusta):
        def fake(pdf_bytes, **kwargs):
            if pdf_bytes == b"BAD":
                raise ExtractionError("corrupt")
            return augusta
        monkeypatch.setattr(batch, "extract_report", fake)

        items = batch.run_batch(
            {"good1.pdf": b"%PDF", "bad.pdf": b"BAD", "good2.pdf": b"%PDF"}, BASELINE)

        by_name = {i.filename: i for i in items}
        assert by_name["bad.pdf"].status == Status.FAILED
        assert by_name["good1.pdf"].status == Status.EXTRACTED
        assert by_name["good2.pdf"].status == Status.EXTRACTED
        assert len(items) == 3

    def test_progress_is_reported_for_every_document(self, monkeypatch, augusta):
        monkeypatch.setattr(batch, "extract_report", lambda **kw: augusta)
        seen = []
        batch.run_batch({"a.pdf": b"1", "b.pdf": b"2"}, BASELINE,
                        on_progress=lambda done, total, item: seen.append((done, total)))
        assert [d for d, _ in seen] == [1, 2]
        assert all(t == 2 for _, t in seen)

    def test_summary_counts_by_status(self, monkeypatch, augusta):
        def fake(pdf_bytes, **kwargs):
            if pdf_bytes == b"BAD":
                raise ExtractionError("corrupt")
            return augusta
        monkeypatch.setattr(batch, "extract_report", fake)
        items = batch.run_batch({"a.pdf": b"1", "b.pdf": b"BAD"}, BASELINE)
        assert batch.summarise(items) == {Status.EXTRACTED: 1, Status.FAILED: 1}


class TestSaving:
    def test_conflicts_are_held_back_by_default(self, monkeypatch, augusta):
        saved = []
        monkeypatch.setattr(batch, "save_report", lambda *a, **kw: saved.append(kw) or "appended")

        item = BatchItem("x.pdf", status=Status.CONFLICT, syndicate_id="SGB",
                         period_end="2026-03-31", report=augusta)
        batch.save_batch([item])
        assert saved == [], "a disputed syndicate must not be written unattended"
        assert item.status == Status.CONFLICT

    def test_conflicts_save_when_explicitly_included(self, monkeypatch, augusta):
        monkeypatch.setattr(batch, "save_report", lambda *a, **kw: "appended")
        item = BatchItem("x.pdf", status=Status.CONFLICT, syndicate_id="SGB",
                         period_end="2026-03-31", report=augusta)
        batch.save_batch([item], include_conflicts=True)
        assert item.status == Status.SAVED

    def test_unmatched_items_are_never_saved(self, monkeypatch, augusta):
        saved = []
        monkeypatch.setattr(batch, "save_report", lambda *a, **kw: saved.append(1) or "appended")
        item = BatchItem("x.pdf", status=Status.UNMATCHED, syndicate_id=None,
                         period_end="2026-03-31", report=augusta)
        batch.save_batch([item])
        assert saved == []

    def test_a_failed_save_does_not_stop_the_rest(self, monkeypatch, augusta):
        calls = {"n": 0}

        def fake(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("sheet locked")
            return "appended"

        monkeypatch.setattr(batch, "save_report", fake)
        items = [
            BatchItem("a.pdf", status=Status.EXTRACTED, syndicate_id="SGB",
                      period_end="2026-03-31", report=augusta),
            BatchItem("b.pdf", status=Status.EXTRACTED, syndicate_id="CENT-GOVT1",
                      period_end="2026-03-31", report=augusta),
        ]
        batch.save_batch(items)
        assert items[0].status == Status.FAILED
        assert items[1].status == Status.SAVED
