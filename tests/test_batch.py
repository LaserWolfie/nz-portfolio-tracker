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


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never read or write the real extraction cache from a test.

    Without this, one test's cached report satisfies the next test that happens to
    use the same fake PDF bytes, and a deliberately failing extraction quietly passes.
    """
    monkeypatch.setattr(batch, "DEFAULT_CACHE_DIR", str(tmp_path / "cache"))


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


class TestDocumentKind:
    """Sampled from the real Drive folders: half the documents are not reports."""

    @pytest.mark.parametrize("filename", [
        "Sir-William-Pickering-Dr-Biannual-Report-30-September-2025.pdf",
        "33-Broadway-FY2025-Annual-Report.pdf",
        "Augusta-St-Georges-Bay-Rd-Biannual-Report-31-March-2025.pdf",
    ])
    def test_periodic_reports(self, filename):
        assert batch.document_kind(filename) == "report"

    @pytest.mark.parametrize("filename", [
        "Birch Ave - Proxy Voting Form.pdf",
        "Sir-William-Pickering-Dr-Notice-of-Special-Meeting-2025.pdf",
        "33-Broadway-Product-Disclosure-Statement.pdf",
        "Governing_Document.pdf",
        "SIPO_Sir_William_Pickering_Drive_Limited_Partnership.pdf",
        "33-Broadway-Trust-Annual-Meeting-Presentation-2025.pdf",
    ])
    def test_administrative_documents(self, filename):
        assert batch.document_kind(filename) == "administrative"

    def test_valuation_update_is_supporting_not_administrative(self):
        """It carries a real valuation, so it is not skipped by default."""
        assert batch.document_kind("33-Broadway-Valuation-Update-April-2025.pdf") == "supporting"

    def test_administrative_documents_are_excluded_from_a_run(self):
        items = plan_batch([
            "33-Broadway-FY2025-Annual-Report.pdf",
            "Birch Ave - Proxy Voting Form.pdf",
        ], BASELINE)
        kept = batch.worth_extracting(items)
        assert [i.filename for i in kept] == ["33-Broadway-FY2025-Annual-Report.pdf"]

    def test_biannual_is_stripped_when_matching(self):
        """NZ syndicates report half-yearly, so 'Biannual' is on most reports."""
        assert "Biannual" not in name_from_filename(
            "33-Broadway-Biannual-Report-30-September-2025.pdf")


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


class TestExtractionCache:
    """Extraction costs money; saving does not. A re-run must never pay twice."""

    def _counting_extractor(self, monkeypatch, report):
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            return report

        monkeypatch.setattr(batch, "extract_report", fake)
        return calls

    def test_second_run_reads_the_cache_instead_of_the_api(self, monkeypatch, augusta, tmp_path):
        calls = self._counting_extractor(monkeypatch, augusta)
        cache = str(tmp_path / "c")

        first = batch.extract_one(BatchItem("augusta.pdf"), b"%PDF-bytes", BASELINE,
                                  cache_dir=cache)
        second = batch.extract_one(BatchItem("augusta.pdf"), b"%PDF-bytes", BASELINE,
                                   cache_dir=cache)

        assert len(calls) == 1, "the second run must not call the API"
        assert first.from_cache is False and second.from_cache is True
        assert second.period_end == first.period_end == "2026-03-31"
        assert second.syndicate_id == "SGB"

    def test_a_renamed_file_still_hits_the_cache(self, monkeypatch, augusta, tmp_path):
        """The key is the bytes, so re-filing a document does not make it cost again."""
        calls = self._counting_extractor(monkeypatch, augusta)
        cache = str(tmp_path / "c")
        batch.extract_one(BatchItem("SGBR_FY26.pdf"), b"%PDF-bytes", BASELINE, cache_dir=cache)
        item = batch.extract_one(BatchItem("Augusta - Annual Report 2026.pdf"), b"%PDF-bytes",
                                 BASELINE, cache_dir=cache)
        assert len(calls) == 1
        assert item.from_cache is True

    def test_a_different_model_is_not_a_hit(self, monkeypatch, augusta, tmp_path):
        """A cross-check run must never read the first model's answer."""
        calls = self._counting_extractor(monkeypatch, augusta)
        cache = str(tmp_path / "c")
        batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE,
                          model="claude-opus-5", cache_dir=cache)
        item = batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE,
                                 model="claude-fable-5", cache_dir=cache)
        assert len(calls) == 2
        assert item.from_cache is False

    def test_different_documents_do_not_collide(self, monkeypatch, augusta, tmp_path):
        calls = self._counting_extractor(monkeypatch, augusta)
        cache = str(tmp_path / "c")
        batch.extract_one(BatchItem("a.pdf"), b"%PDF-one", BASELINE, cache_dir=cache)
        batch.extract_one(BatchItem("b.pdf"), b"%PDF-two", BASELINE, cache_dir=cache)
        assert len(calls) == 2

    def test_a_corrupt_cache_file_is_a_miss_not_a_crash(self, monkeypatch, augusta, tmp_path):
        calls = self._counting_extractor(monkeypatch, augusta)
        cache = tmp_path / "c"
        cache.mkdir()
        (cache / batch.cache_key(b"%PDF-bytes", batch.DEFAULT_MODEL)).write_text("{ not json")

        item = batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE,
                                 cache_dir=str(cache))
        assert len(calls) == 1
        assert item.status == Status.EXTRACTED

    def test_caching_can_be_turned_off(self, monkeypatch, augusta, tmp_path):
        calls = self._counting_extractor(monkeypatch, augusta)
        batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE, cache_dir=None)
        batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE, cache_dir=None)
        assert len(calls) == 2

    def test_an_unwritable_cache_does_not_sink_the_run(self, monkeypatch, augusta, tmp_path):
        """A full or read-only disk must cost time, never the extraction itself."""
        self._counting_extractor(monkeypatch, augusta)

        def explode(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(batch.os, "replace", explode)
        item = batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE,
                                 cache_dir=str(tmp_path / "c"))
        assert item.status == Status.EXTRACTED
        assert item.report is not None

    def test_a_failed_save_leaves_the_report_cached(self, monkeypatch, augusta, tmp_path):
        """The whole point: a Sheets failure must not force a paid re-extraction."""
        self._counting_extractor(monkeypatch, augusta)
        cache = str(tmp_path / "c")
        item = batch.extract_one(BatchItem("a.pdf"), b"%PDF-bytes", BASELINE, cache_dir=cache)

        monkeypatch.setattr(batch, "save_report",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("sheets down")))
        batch.save_batch([item])

        assert item.status == Status.FAILED
        assert batch.load_cached(cache, b"%PDF-bytes", batch.DEFAULT_MODEL) is not None
        assert "cached" in item.error


class TestSaveRetry:
    """The quota error that cost a paid re-extraction the first time."""

    def _item(self, augusta):
        return BatchItem("a.pdf", status=Status.EXTRACTED, syndicate_id="SGB",
                         period_end="2026-03-31", report=augusta)

    def test_a_quota_error_is_retried_and_succeeds(self, monkeypatch, augusta):
        monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
        attempts = []

        def flaky(*args, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("APIError: [429]: Quota exceeded for read requests per minute")
            return "appended"

        monkeypatch.setattr(batch, "save_report", flaky)
        item = self._item(augusta)
        batch.save_batch([item])

        assert item.status == Status.SAVED
        assert len(attempts) == 3
        assert any("rate limit" in note for note in item.notes)

    def test_a_real_error_is_not_retried(self, monkeypatch, augusta):
        """Retrying a malformed row just wastes a minute before failing anyway."""
        monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
        attempts = []

        def broken(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError("row_from_dict: unknown key 'occupancy'")

        monkeypatch.setattr(batch, "save_report", broken)
        item = self._item(augusta)
        batch.save_batch([item])

        assert item.status == Status.FAILED
        assert len(attempts) == 1

    def test_it_gives_up_rather_than_retrying_forever(self, monkeypatch, augusta):
        monkeypatch.setattr(batch.time, "sleep", lambda seconds: None)
        attempts = []

        def always_limited(*args, **kwargs):
            attempts.append(1)
            raise RuntimeError("429 quota exceeded")

        monkeypatch.setattr(batch, "save_report", always_limited)
        item = self._item(augusta)
        batch.save_batch([item])

        assert item.status == Status.FAILED
        assert len(attempts) == batch.SAVE_MAX_ATTEMPTS


class TestCostPreview:
    def test_cached_documents_are_not_counted_as_cost(self, monkeypatch, augusta, tmp_path):
        """A re-run after a failure should quote what it will really cost."""
        cache = str(tmp_path / "c")
        monkeypatch.setattr(batch, "extract_report", lambda **kw: augusta)
        batch.extract_one(BatchItem("a.pdf"), b"%PDF-a", BASELINE, cache_dir=cache)

        documents = {"a.pdf": b"%PDF-a", "b.pdf": b"%PDF-b"}
        cached = batch.already_cached(documents, cache_dir=cache)

        assert cached == {"a.pdf"}
        plan = [BatchItem("a.pdf"), BatchItem("b.pdf")]
        unpaid = [i for i in plan if i.filename not in cached]
        assert batch.estimated_cost(unpaid) == pytest.approx(batch.ESTIMATED_COST_PER_DOCUMENT)

    def test_no_cache_means_everything_costs(self, augusta):
        documents = {"a.pdf": b"%PDF-a", "b.pdf": b"%PDF-b"}
        assert batch.already_cached(documents, cache_dir=None) == set()


class TestFilenameQuirksFromTheDryRun:
    """Filenames that matched nothing when the September intake was rehearsed."""

    ROWS = [
        {"syndicate_id": "ESKI-DAYCARE", "canonical_name": "E+O N.Z. Daycare Fund",
         "aliases": "NZ Daycare Properties Fund LP"},
        {"syndicate_id": "CENT-AIRWAYSSOE",
         "canonical_name": "Sir William Pickering Drive Limited Partnership",
         "aliases": "SWPD"},
        {"syndicate_id": "SILV-SURPLUSBROKE", "canonical_name": "Surplus Brokers",
         "aliases": "22SYR|SYR"},
    ]

    def test_plus_separated_filenames_are_read(self):
        """Erskine & Owen's portal joins every word with '+'."""
        name = name_from_filename("EO+Quarterly+Report_FY26_Q4_NZ+Daycare+Properties+Fund+LP.pdf")
        assert "Daycare" in name
        assert plan_batch([
            "EO+Quarterly+Report_FY26_Q4_NZ+Daycare+Properties+Fund+LP.pdf"
        ], self.ROWS)[0].syndicate_id == "ESKI-DAYCARE"

    def test_period_tags_are_stripped_in_every_shape(self):
        assert name_from_filename("FY26 Q1 22SYR.pdf") == "22SYR"
        assert name_from_filename("SWPD_-_FY26_Annual_Report.pdf") == "SWPD"
        assert "FY27Q1" not in name_from_filename("EO+Report+FY27Q1+Montreal.pdf")

    def test_an_abbreviation_matches_through_its_alias(self):
        assert plan_batch(["SWPD_-_FY26_Annual_Report.pdf"],
                          self.ROWS)[0].syndicate_id == "CENT-AIRWAYSSOE"

    def test_meaningful_numbers_still_survive_the_tag_stripping(self):
        """'22SYR' and '33 Broadway' must not lose their digits to a period tag regex."""
        assert "22SYR" in name_from_filename("FY26 Q1 22SYR.pdf")
        assert "33" in name_from_filename("33_Broadway_-_FY26_Annual_Report.pdf")
