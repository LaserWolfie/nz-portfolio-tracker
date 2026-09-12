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
"""

import os
import re
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


def extract_one(item: BatchItem, pdf_bytes: bytes, baseline_rows: list[dict],
                api_key: str | None = None, model: str = DEFAULT_MODEL) -> BatchItem:
    """Extract a single document, recording failure rather than raising.

    Never lets an exception escape: a batch of thirty must not stop on one bad
    file, and a document that fails still deserves a readable reason.
    """
    try:
        report = extract_report(
            pdf_bytes=pdf_bytes,
            api_key=api_key,
            syndicate_name=_canonical_name(item.syndicate_id, baseline_rows),
            model=model,
        )
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


def run_batch(documents: dict[str, bytes], baseline_rows: list[dict],
              api_key: str | None = None, model: str = DEFAULT_MODEL,
              on_progress=None) -> list[BatchItem]:
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
                extract_one, item, documents[item.filename], baseline_rows, api_key, model
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
            item.save_result = save_report(
                item.report,
                syndicate_id=item.syndicate_id,
                source_filename=item.filename,
                model=model,
                spreadsheet=spreadsheet,
            )
            item.status = Status.SAVED
        except Exception as e:  # noqa: BLE001 - one bad write must not stop the rest
            item.status = Status.FAILED
            item.error = f"Save failed: {e}"
    return items


def summarise(items: list[BatchItem]) -> dict:
    """Counts by status, for a one-line report of how the run went."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts
