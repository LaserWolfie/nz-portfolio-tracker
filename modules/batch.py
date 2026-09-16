"""Batch intake: a whole quarter's investor reports in one run.

Three things this module is careful about.

**Match before spending.** `plan_batch()` matches filenames to syndicates with
no API calls at all, so a whole quarter can be previewed -- and mis-filed or
unrecognised documents caught -- before any money is spent. Extraction is the
expensive step; filename matching is free.

**Per-file error isolation.** One corrupt or image-only PDF must not sink the
other twenty-nine. Every document is extracted inside its own try/except and
records its own outcome.

**The document outranks the filename.** A filename is a convenience; the entity
name printed inside the report is evidence. Where the two disagree the extracted
name wins and the conflict is surfaced, because a file named for last quarter's
syndicate is a filing error waiting to corrupt two histories.

**Extraction costs money; saving does not.** Every report is written to the cache
the moment it returns, so a Sheets outage, a rate limit or a crash part-way through
a thirty-document quarter never forces a paid re-extraction. A re-run reads the
cache and calls nothing. This was learned the hard way: one run saved 7 of 9 rows,
hit `429 Quota exceeded` from the Sheets API, and the reports already paid for
existed only in memory.
"""

import hashlib
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from modules.extraction import DEFAULT_MODEL, ExtractionError, completeness, extract_report
from modules.schema import SyndicateReport
from modules.storage import resolve_syndicate_id, save_report

#: Documents processed at once. Each runs two concurrent extraction passes, so
#: this is half the number of in-flight API requests. Kept modest to stay well
#: clear of rate limits on a 30-document quarter.
MAX_CONCURRENT_DOCUMENTS = 3

#: Rough cost of one document, both passes, on claude-opus-5. Used only to warn
#: before a run; never for billing.
ESTIMATED_COST_PER_DOCUMENT = 0.80

#: Where extracted reports are cached. Override with the NZWM_EXTRACT_CACHE
#: environment variable, or pass `cache_dir`; pass None to disable caching.
DEFAULT_CACHE_DIR = os.environ.get(
    "NZWM_EXTRACT_CACHE", os.path.join(tempfile.gettempdir(), "nz_wealth_extract_cache")
)

#: A save that trips the Sheets per-minute quota is worth retrying: the extraction
#: behind it has already been paid for.
SAVE_MAX_ATTEMPTS = 4
SAVE_BACKOFF_SECONDS = 2.0

#: Substrings that mark a save error as rate limiting rather than a real failure.
RETRYABLE_SAVE_ERRORS = ("429", "quota exceeded", "rate limit", "try again later")

#: Sentinel: resolve DEFAULT_CACHE_DIR when the call is made, not when this module
#: is imported, so redirecting the cache (a test, or a run pointed at a project
#: folder) takes effect without every caller having to pass it through.
_USE_DEFAULT_CACHE = object()


class Status:
    PENDING = "pending"
    MATCHED = "matched"          # filename resolved to a syndicate
    UNMATCHED = "unmatched"      # no confident match; needs a human
    EXTRACTED = "extracted"      # figures retrieved
    SPARSE = "sparse"            # too few figures to trust as a period row
    CONFLICT = "conflict"        # filename and document disagree
    SAVED = "saved"
    FAILED = "failed"


#: Below this share of figures a document is treated as the wrong kind of
#: document rather than a thin report. A tax statement filed for the same
#: syndicate and period as a real report yields about an eighth of the fields;
#: saving it would later read as the manager having withdrawn disclosure on
#: everything it lacks.
SPARSE_THRESHOLD = 0.5


@dataclass
class BatchItem:
    """One document's journey through the run."""

    filename: str
    status: str = Status.PENDING
    kind: str = "supporting"
    syndicate_id: str | None = None
    filename_match: str | None = None
    extracted_match: str | None = None
    entity_name: str | None = None
    period_end: str | None = None
    report: SyndicateReport | None = None
    error: str | None = None
    save_result: str | None = None
    completeness: tuple[int, int] | None = None
    from_cache: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return self.status in (
            Status.FAILED, Status.CONFLICT, Status.UNMATCHED, Status.SPARSE,
        )


def name_from_filename(filename: str) -> str:
    """Turn a filename into something a syndicate matcher can read.

    Strips the extension and the date and report-type words managers append,
    so 'Augusta_St_Georges_Bay_Road_Property_Trust_Annual_Report_June_2026.pdf'
    reduces to the entity name.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    text = stem.replace("_", " ").replace("-", " ")
    noise = {
        # Report types. "biannual" matters: NZ syndicates report half-yearly at
        # 31 March and 30 September, so it appears on most periodic reports.
        "annual", "biannual", "half", "yearly", "report", "quarterly", "quarter",
        "update", "interim", "financial", "statements", "statement", "investor",
        "valuation", "tax", "holding", "holdings",
        # Document furniture.
        "notice", "meeting", "special", "presentation", "proxy", "voting",
        "form", "cover", "letter", "disclosure", "product", "governing",
        "document", "sipo", "minutes", "booklet",
        "final", "draft", "signed", "copy", "v1", "v2", "v3",
        "fy", "q1", "q2", "q3", "q4",
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
    }
    words = []
    for word in text.split():
        lower = word.lower()
        if lower in noise or lower.startswith("fy20"):
            continue
        # Drop years only. Other numbers are part of the name -- "33 Broadway",
        # "Govt Income 1", "100 Harris Road" -- and stripping them makes
        # Govt Income 1 and 2 indistinguishable.
        if re.fullmatch(r"(19|20)\d{2}", word):
            continue
        words.append(word)
    return " ".join(words).strip()


#: Filename markers for documents that carry no periodic figures. Extracting a
#: proxy voting form costs the same as extracting an annual report and yields
#: nothing, so they are flagged before any money is spent rather than after.
ADMINISTRATIVE_MARKERS = (
    "proxy", "voting form", "notice of", "product disclosure", "governing document",
    "sipo", "presentation", "minutes", "certificate", "cover letter",
    "resolution has passed", "campaign has closed",
)

#: Markers for the periodic reports the pipeline exists to read.
REPORT_MARKERS = (
    "annual report", "biannual report", "interim report", "quarterly report",
    "half year", "financial statements", "biannual",
)


def document_kind(filename: str) -> str:
    """Guess from the filename alone whether this is worth extracting.

    'report'         -- a periodic investor report.
    'administrative' -- proxy forms, meeting notices, PDSs, governing documents.
    'supporting'     -- everything else: valuation updates, tax statements and
                        the like, which carry some figures but not a full period.
    """
    text = os.path.splitext(os.path.basename(filename))[0]
    text = text.replace("_", " ").replace("-", " ").lower()
    if any(marker in text for marker in REPORT_MARKERS):
        return "report"
    if any(marker in text for marker in ADMINISTRATIVE_MARKERS):
        return "administrative"
    return "supporting"


def plan_batch(filenames: list[str], baseline_rows: list[dict]) -> list[BatchItem]:
    """Match filenames to syndicates without calling the API.

    Lets a whole quarter be reviewed -- and the cost understood -- before any
    extraction runs.
    """
    items = []
    for filename in filenames:
        item = BatchItem(filename=filename)
        item.kind = document_kind(filename)
        if item.kind == "administrative":
            item.notes.append(
                "Looks administrative (proxy form, meeting notice, disclosure "
                "statement). Probably carries no periodic figures."
            )
        item.filename_match = resolve_syndicate_id(
            name_from_filename(filename), baseline_rows
        )
        if item.filename_match:
            item.syndicate_id = item.filename_match
            item.status = Status.MATCHED
        else:
            item.status = Status.UNMATCHED
            item.notes.append(
                "Filename did not match a known syndicate; the document's own entity "
                "name will be used instead."
            )
        items.append(item)
    return items


def estimated_cost(items: list[BatchItem]) -> float:
    return len(items) * ESTIMATED_COST_PER_DOCUMENT


def worth_extracting(items: list[BatchItem]) -> list[BatchItem]:
    """Everything except the documents that look purely administrative."""
    return [i for i in items if i.kind != "administrative"]


def cache_key(pdf_bytes: bytes, model: str) -> str:
    """Content hash plus model.

    Keyed on the bytes, not the filename, so a renamed file still hits and two
    copies of one report are extracted once. The model is part of the key because a
    cross-check run under a different model must never read another model's answer.
    """
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model or "default")
    return f"{digest}_{safe_model}.json"


def load_cached(cache_dir: str | None, pdf_bytes: bytes,
                model: str = DEFAULT_MODEL) -> SyndicateReport | None:
    """The cached report, or None. Never raises: a bad cache is a miss, not a failure."""
    if not cache_dir:
        return None
    path = os.path.join(cache_dir, cache_key(pdf_bytes, model))
    try:
        with open(path, encoding="utf-8") as handle:
            return SyndicateReport.model_validate_json(handle.read())
    except Exception:  # noqa: BLE001 - absent, unreadable or stale: extract again
        return None


def store_cached(cache_dir: str | None, pdf_bytes: bytes, report: SyndicateReport,
                 model: str = DEFAULT_MODEL) -> str | None:
    """Write a report to the cache. Never raises: caching must not sink a run.

    Written to a temporary file and moved into place, so an interrupted write cannot
    leave a half-written file that later reads as a cache hit.
    """
    if not cache_dir:
        return None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, cache_key(pdf_bytes, model))
        temp = f"{path}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(report.model_dump_json())
        os.replace(temp, path)
        return path
    except Exception:  # noqa: BLE001 - a full or read-only disk must not stop extraction
        return None


def extract_one(item: BatchItem, pdf_bytes: bytes, baseline_rows: list[dict],
                api_key: str | None = None, model: str = DEFAULT_MODEL,
                cache_dir: str | None = _USE_DEFAULT_CACHE) -> BatchItem:
    """Extract a single document, recording failure rather than raising.

    Never lets an exception escape: a batch of thirty must not stop on one bad
    file, and a document that fails still deserves a readable reason.
    """
    if cache_dir is _USE_DEFAULT_CACHE:
        cache_dir = DEFAULT_CACHE_DIR

    report = load_cached(cache_dir, pdf_bytes, model)
    if report is not None:
        item.from_cache = True
        item.notes.append("Loaded from the extraction cache; no API call was made.")
    try:
        if report is None:
            report = extract_report(
                pdf_bytes=pdf_bytes,
                api_key=api_key,
                syndicate_name=_canonical_name(item.syndicate_id, baseline_rows),
                model=model,
            )
            # Cached before anything else touches it: from here on a crash, a rate
            # limit or a failed save costs time, not money.
            store_cached(cache_dir, pdf_bytes, report, model)
    except ExtractionError as e:
        item.status = Status.FAILED
        item.error = str(e)
        return item
    except Exception as e:  # noqa: BLE001 - isolation is the point
        item.status = Status.FAILED
        item.error = f"Unexpected error: {e}"
        return item

    item.report = report
    item.entity_name = report.identity.entity_name.value
    item.period_end = report.identity.period_end_date.value
    item.completeness = completeness(report)
    item.extracted_match = resolve_syndicate_id(item.entity_name or "", baseline_rows)

    _reconcile(item)

    found, total = item.completeness
    if found == 0:
        item.status = Status.FAILED
        item.error = "No figures extracted; the PDF may be image-only or not a report."
    elif total and found < total * SPARSE_THRESHOLD:
        # Held back from saving rather than merely noted: a tax statement or
        # valuation letter filed against a real period would look, next quarter,
        # like the manager had stopped reporting everything it happens to omit.
        if item.status != Status.CONFLICT:
            item.status = Status.SPARSE
        item.notes.append(
            f"Only {found} of {total} figures found. This may be a tax statement or "
            "valuation letter rather than a full investor report."
        )

    if not item.period_end:
        item.status = Status.FAILED
        item.error = "No period end date found, so the report cannot be filed."

    return item


def _reconcile(item: BatchItem) -> None:
    """Decide which syndicate the document belongs to, and say why."""
    if item.extracted_match and item.filename_match:
        if item.extracted_match == item.filename_match:
            item.syndicate_id = item.extracted_match
            item.status = Status.EXTRACTED
        else:
            # The document is evidence; the filename is a label someone typed.
            item.syndicate_id = item.extracted_match
            item.status = Status.CONFLICT
            item.notes.append(
                f"Filename suggests {item.filename_match} but the document says "
                f"{item.extracted_match}. Using the document; confirm before saving."
            )
    elif item.extracted_match:
        item.syndicate_id = item.extracted_match
        item.status = Status.EXTRACTED
    elif item.filename_match:
        item.syndicate_id = item.filename_match
        item.status = Status.EXTRACTED
        item.notes.append(
            f"Matched on filename only -- the entity name in the document "
            f"({item.entity_name!r}) did not resolve. Consider adding it as an alias."
        )
    else:
        item.syndicate_id = None
        item.status = Status.UNMATCHED
        item.notes.append(
            f"Could not match {item.entity_name!r} to any syndicate. Assign it by hand "
            "or add an alias in Syndicate_Baseline."
        )


def _canonical_name(syndicate_id: str | None, baseline_rows: list[dict]) -> str | None:
    if not syndicate_id:
        return None
    for row in baseline_rows:
        if str(row.get("syndicate_id")) == str(syndicate_id):
            return str(row.get("canonical_name")) or None
    return None


def already_cached(documents: dict[str, bytes], model: str = DEFAULT_MODEL,
                   cache_dir: str | None = _USE_DEFAULT_CACHE) -> set[str]:
    """Filenames whose extraction is already on disk, so a run would not pay for them.

    Lets the cost preview say what the run will actually cost rather than what a
    cold run would, which matters most when re-running after a failure.
    """
    if cache_dir is _USE_DEFAULT_CACHE:
        cache_dir = DEFAULT_CACHE_DIR
    if not cache_dir:
        return set()
    return {
        name for name, pdf_bytes in documents.items()
        if os.path.exists(os.path.join(cache_dir, cache_key(pdf_bytes, model)))
    }


def run_batch(documents: dict[str, bytes], baseline_rows: list[dict],
              api_key: str | None = None, model: str = DEFAULT_MODEL,
              on_progress=None,
              cache_dir: str | None = _USE_DEFAULT_CACHE) -> list[BatchItem]:
    """Extract every document. `documents` maps filename to PDF bytes.

    Returns one BatchItem per document, in the order given, whatever happened to
    each. `on_progress(done, total, item)` is called as each finishes.
    """
    items = plan_batch(list(documents), baseline_rows)
    total = len(items)
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOCUMENTS) as pool:
        futures = {
            pool.submit(
                extract_one, item, documents[item.filename], baseline_rows, api_key,
                model, cache_dir,
            ): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
            except Exception as e:  # noqa: BLE001 - belt and braces
                item.status = Status.FAILED
                item.error = f"Unexpected error: {e}"
            done += 1
            if on_progress:
                on_progress(done, total, item)

    return items


def save_batch(items: list[BatchItem], spreadsheet=None, model: str = DEFAULT_MODEL,
               include_conflicts: bool = False,
               include_sparse: bool = False) -> list[BatchItem]:
    """Persist every item that is ready. Disputed and thin ones are held back.

    A conflict means the filename and the document disagree about which
    syndicate this is; saving one under the wrong id corrupts two histories.
    A sparse document is usually the wrong kind of document altogether. Both
    wait for a human unless explicitly included.
    """
    for item in items:
        if item.report is None or not item.syndicate_id or not item.period_end:
            continue
        if item.status == Status.CONFLICT and not include_conflicts:
            continue
        if item.status == Status.SPARSE and not include_sparse:
            continue
        if item.status == Status.SAVED:
            continue
        try:
            item.save_result = _save_with_retry(item, model, spreadsheet)
            item.status = Status.SAVED
        except Exception as e:  # noqa: BLE001 - one bad write must not stop the rest
            item.status = Status.FAILED
            item.error = f"Save failed: {e}. The extraction is cached, so re-running "
            item.error += "saves it without paying to extract it again."
    return items


def _is_retryable(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in RETRYABLE_SAVE_ERRORS)


def _save_with_retry(item: BatchItem, model: str, spreadsheet) -> str:
    """Save one row, backing off on the Sheets per-minute quota.

    Only rate limiting is retried. A malformed row or a missing column is a real
    error and retrying it just wastes a minute before failing anyway.
    """
    delay = SAVE_BACKOFF_SECONDS
    for attempt in range(1, SAVE_MAX_ATTEMPTS + 1):
        try:
            return save_report(
                item.report,
                syndicate_id=item.syndicate_id,
                source_filename=item.filename,
                model=model,
                spreadsheet=spreadsheet,
            )
        except Exception as error:  # noqa: BLE001 - classified, then re-raised or retried
            if attempt == SAVE_MAX_ATTEMPTS or not _is_retryable(error):
                raise
            item.notes.append(
                f"Save attempt {attempt} hit a rate limit; retrying in {delay:.0f}s."
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")  # pragma: no cover


def summarise(items: list[BatchItem]) -> dict:
    """Counts by status, for a one-line report of how the run went."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts
