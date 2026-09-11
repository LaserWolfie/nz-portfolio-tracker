"""Schema-constrained extraction of syndicate reports from PDF.

Deliberately free of Streamlit imports so the batch intake in Phase 4 and the
tests can both call it directly.

The PDF is sent to the model as a `document` content block, never pre-extracted
to text. Layout carries meaning in these reports -- figures live in tables with
column headings like "Current / Prior / Forecast", and flattening to text is
what makes a model take the wrong column.
"""

import base64

import anthropic

from modules.schema import (
    EXTRACTION_SYSTEM_PROMPT,
    DateFigure,
    Figure,
    SyndicateReport,
    TextFigure,
    build_extraction_prompt,
)

#: The wrapper types that represent one extracted value with provenance.
FIGURE_TYPES = (Figure, DateFigure, TextFigure)

DEFAULT_MODEL = "claude-opus-5"

# Generous ceiling: 23 fields, each with a verbatim quote, plus adaptive thinking
# tokens which count against this limit.
MAX_TOKENS = 16000

# Reports run to tens of pages; allow well beyond the 10 minute default.
REQUEST_TIMEOUT_SECONDS = 900.0


class ExtractionError(Exception):
    """Raised when a document could not be extracted into the schema."""


def extract_report(
    pdf_bytes: bytes,
    api_key: str | None = None,
    syndicate_name: str | None = None,
    model: str = DEFAULT_MODEL,
) -> SyndicateReport:
    """Extract one investor report PDF into the fixed schema.

    `api_key` is optional. When it is omitted the SDK resolves credentials
    itself, in order: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, an `ant auth
    login` OAuth profile, then Workload Identity Federation. That keeps the
    Streamlit Cloud path (a key in secrets) and a local keyless path working
    from the same code.

    `syndicate_name` is the syndicate we believe this document belongs to, used
    only to let the model flag a mismatch -- it never overrides the document.

    Raises ExtractionError with a readable message; the caller decides whether
    one bad document should stop a batch.
    """
    client = (
        anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        if api_key
        else anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    )
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=EXTRACTION_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=SyndicateReport,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": build_extraction_prompt(syndicate_name),
                        },
                    ],
                }
            ],
        )
    except anthropic.BadRequestError as e:
        raise ExtractionError(f"Request rejected: {e.message}") from e
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key is invalid or missing.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the Anthropic API; retry shortly.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API; check the network.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to process this document.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError(
            "Extraction hit the output limit before finishing; the document is unusually "
            "long. Raise MAX_TOKENS or split the PDF."
        )

    report = response.parsed_output
    if report is None:
        raise ExtractionError("The model returned no structured output.")
    return report


def completeness(report: SyndicateReport) -> tuple[int, int]:
    """How many of the single-figure fields came back populated.

    Returns (found, total). A low ratio means the document was scanned, image-only,
    or is not the report type we expect -- worth surfacing before the figures are
    trusted or stored.
    """
    found = 0
    total = 0
    for name in report.__class__.model_fields:
        field = getattr(report, name)
        # Explicit type check, not hasattr("value"): enum members carry a .value
        # too, which would count distribution_unit as a populated figure.
        if isinstance(field, FIGURE_TYPES):
            total += 1
            if field.value is not None:
                found += 1
    return found, total
