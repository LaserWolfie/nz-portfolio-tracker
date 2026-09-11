"""Per-syndicate notes and draft manager questions.

**Generated only from the stored row, its flags and the baseline -- never from
the PDF.** The document is read once, by the extractor, under a schema. Letting
a second model loose on the source would produce commentary that cannot be
traced back to a figure, and the whole point of the pipeline is that every
statement rests on a stored number with a page reference behind it.

`build_facts()` is pure and enforces that boundary: it assembles exactly what
the writer is allowed to see. It is tested directly, so the guarantee does not
depend on prompt wording. In particular it excludes `raw_json` and every
`source_text` quote -- those are verbatim prose lifted from the document, and
feeding them back in would smuggle the PDF into the narrative through the side
door.
"""

import json

import anthropic
from pydantic import BaseModel, Field

from modules.deltas import Flag
from modules.extraction import REQUEST_TIMEOUT_SECONDS, ExtractionError
from modules.schema import figure_names

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 4000

#: Columns that are bookkeeping rather than substance.
EXCLUDED_COLUMNS = {
    "raw_json", "source_filename", "extracted_at", "model",
    "completeness_found", "completeness_total",
}

#: Baseline columns worth showing the writer.
BASELINE_COLUMNS = [
    "canonical_name", "manager_name", "sector", "owner_entity",
    "im_forecast_distribution_rate", "im_forecast_distribution_unit",
    "formation_nav_per_unit", "original_investment_per_unit",
    "icr_covenant_threshold", "lvr_covenant_threshold", "trust_deed_notes",
]


class ManagerQuestion(BaseModel):
    """One question to put to the manager, and what it rests on."""

    question: str = Field(
        description="The question, written to be sent to the manager as-is. Specific and "
        "answerable, naming the figure it concerns."
    )
    basis: str = Field(
        description="The stored figure or flag this question arises from, so the reader "
        "can check it. Quote the field name and value, e.g. 'icr_covenant 2.0 with "
        "icr_actual not disclosed'."
    )


class SyndicateNarrative(BaseModel):
    """A short, traceable note on one syndicate for one period."""

    headline: str = Field(
        description="One sentence, under 20 words, stating the single most important "
        "thing about this syndicate this period. If nothing is notable, say so plainly."
    )
    note: str = Field(
        description="Two to four sentences of plain English for the investor. State what "
        "changed and what it means. Use only the figures provided. Do not speculate about "
        "causes the data does not show, and do not offer investment advice."
    )
    questions: list[ManagerQuestion] = Field(
        description="Between zero and five questions for the manager, most important "
        "first. Ask only what the provided figures actually raise. An empty list is the "
        "right answer for a quiet period -- do not invent questions to fill space."
    )
    data_gaps: list[str] = Field(
        description="Figures a full investor report would normally disclose that are "
        "absent here, phrased as short noun phrases. Empty list if nothing is missing."
    )


NARRATIVE_SYSTEM_PROMPT = """\
You write short, factual notes for an investor reviewing New Zealand property syndicates.

You are given structured figures already extracted from an investor report, the changes
against the prior period, and ranked flags produced by a rule engine. You are NOT given
the report itself, and you must not ask for it.

Rules:

1. Every number you write must appear in the facts you were given. Never introduce a
   figure from anywhere else, and never compute a new one - no ratios, differences,
   percentages or annualisations of your own. The flags already carry the arithmetic that
   matters.

2. A null means the manager did not disclose it. That is a fact worth reporting, not a
   zero and not an omission to paper over.

3. Write for someone deciding where to spend an hour of attention. Lead with what
   actually matters. If the period is unremarkable, say that in one line rather than
   manufacturing concern - a note that cries wolf on a quiet syndicate makes the whole
   review worthless.

4. Questions go to the manager, so make them specific and answerable. "What is the
   interest coverage ratio at balance date, and what headroom does that leave against the
   2.0x covenant?" is useful. "Please comment on performance" is not.

5. Do not give investment advice, and do not recommend buying, holding or selling. Report
   what the figures show and what should be asked.

6. Plain English. No jargon the figures do not already use, and no filler.
"""


def _clean(value):
    """Sheets gives '' for an undisclosed figure; keep that distinct from zero."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def build_facts(row: dict, flags: list[Flag] | None = None,
                prior: dict | None = None, baseline: dict | None = None) -> dict:
    """Assemble exactly what the writer is allowed to see.

    Pure and side-effect free, so the "structured data only" guarantee can be
    asserted in a test rather than trusted to the prompt.
    """
    flags = flags or []
    figures = set(figure_names())

    def figure_block(source: dict) -> dict:
        out = {}
        for name in sorted(figures):
            value = _clean(source.get(name))
            if value is not None:
                out[name] = value
        for extra in ("distribution_unit", "rent_reversion_percent",
                      "effective_facility_expiry", "earliest_swap_expiry",
                      "total_swap_notional", "swap_count",
                      "manager_fee_total_dollars", "manager_fee_total_percent"):
            value = _clean(source.get(extra))
            if value is not None:
                out[extra] = value
        return out

    facts = {
        "syndicate_id": row.get("syndicate_id"),
        "period_end": row.get("period_end"),
        "entity_name": row.get("entity_name_as_reported"),
        "current_period": figure_block(row),
    }

    if prior:
        facts["prior_period"] = {
            "period_end": prior.get("period_end"),
            **figure_block(prior),
        }

    if baseline:
        facts["baseline_from_information_memorandum"] = {
            key: _clean(baseline.get(key))
            for key in BASELINE_COLUMNS
            if _clean(baseline.get(key)) is not None
        }

    facts["flags"] = [
        {"severity": str(f.severity), "code": f.code, "detail": f.message}
        for f in flags
    ]

    # Model-written prose from the extraction step, kept clearly separate so it
    # is read as a caveat about the data rather than as more figures.
    caveats = _clean(row.get("extraction_notes"))
    if caveats:
        facts["data_caveats_from_extraction"] = caveats

    # Figures a full report would normally carry, absent here.
    facts["not_disclosed"] = sorted(
        name for name in figures if _clean(row.get(name)) is None
    )
    return facts


def write_narrative(row: dict, flags: list[Flag] | None = None,
                    prior: dict | None = None, baseline: dict | None = None,
                    api_key: str | None = None,
                    model: str = DEFAULT_MODEL) -> SyndicateNarrative:
    """Draft the note and questions for one syndicate-period."""
    facts = build_facts(row, flags, prior, baseline)

    client = (
        anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        if api_key
        else anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    )

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=NARRATIVE_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            output_format=SyndicateNarrative,
            messages=[{
                "role": "user",
                "content": (
                    "Write the note for this syndicate-period.\n\n"
                    f"{json.dumps(facts, indent=2, default=str)}"
                ),
            }],
        )
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Narrative failed ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to write this note.")

    narrative = response.parsed_output
    if narrative is None:
        raise ExtractionError("The model returned no narrative.")
    return narrative


def unsupported_numbers(narrative: SyndicateNarrative, facts: dict) -> list[str]:
    """Numbers in the narrative that do not appear in the facts it was given.

    A cheap audit of rule 1. Not proof of correctness -- a figure can be quoted
    accurately but applied wrongly -- but it catches invention, which is the
    failure that would quietly destroy trust in the whole review.
    """
    import re

    def tokens(text: str) -> list[str]:
        # Strip thousands separators FIRST. Splitting "$115,000,000" on commas
        # yields "115", "000", "000", none of which match the stored
        # 115000000, so every large figure would look invented.
        return re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3})", "", text))

    def variants(token: str) -> set[str]:
        out = {token}
        if "." in token:
            out.add(token.rstrip("0").rstrip("."))
        return out

    allowed = set()
    for token in tokens(json.dumps(facts, default=str)):
        allowed |= variants(token)

    written = " ".join(
        [narrative.headline, narrative.note]
        + [q.question for q in narrative.questions]
        + [q.basis for q in narrative.questions]
        + list(narrative.data_gaps)
    )

    unsupported = []
    for token in tokens(written):
        if not variants(token) & allowed:
            unsupported.append(token)
    return unsupported
