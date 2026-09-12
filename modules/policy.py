"""Extraction of scheme policy documents (SIPOs) into the IM baseline.

A SIPO states what the manager **promised**, where a periodic report states what
they **delivered**. Comparing the two is the sharpest available test of whether a
manager is doing their job, so the promises need the same discipline as the
figures: every field required and nullable, every value carrying a page and a
verbatim quote.

This is a separate schema from `SyndicateReport` on purpose. Running a SIPO
through the report schema returns near-zero completeness and trips
`SPARSE_THRESHOLD`, because they are different documents answering different
questions.

Grouped for the same reason `SyndicateReport` is -- see the grammar limit note
in CLAUDE.md. Add new fields inside a group.
"""

import base64

import anthropic
from pydantic import BaseModel, Field

from modules.schema import DateFigure, Figure, TextFigure

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 600.0


class PolicyIdentity(BaseModel):
    """Which scheme this policy belongs to, and when it was set."""

    scheme_name: TextFigure = Field(
        description="Full name of the scheme or fund this SIPO governs, as printed."
    )
    manager_name: TextFigure = Field(description="The manager named in the document.")
    sipo_date: DateFigure = Field(
        description="Date this SIPO was adopted, last reviewed or last amended, in ISO "
        "YYYY-MM-DD form. Null if the document carries no date."
    )


class ReturnTargets(BaseModel):
    """What the manager promised investors would receive."""

    minimum_cash_return_percent: Figure = Field(
        description="The minimum cash return the manager undertakes to provide, as a "
        "whole percent per annum, e.g. 7.0 for '7% per annum'. This is the headline "
        "promise investors are owed."
    )
    cash_return_basis: TextFigure = Field(
        description="What that return is measured against, quoted from the document -- "
        "typically the investor's original equity or original investment. Critical: a "
        "return on original equity is not comparable with one on current value."
    )
    occupancy_floor_percent: Figure = Field(
        description="The minimum property occupancy the manager undertakes to maintain, "
        "as a whole percent, e.g. 90.0 for 'occupancy greater than 90%'."
    )
    nta_floor_percent: Figure = Field(
        description="The floor on net tangible assets, expressed as a whole percent of "
        "NTA at acquisition, e.g. 85.0 for 'not less than 85% of NTA when the Property "
        "was acquired'."
    )


class DebtPolicy(BaseModel):
    """The limits the manager set on gearing and interest risk."""

    lvr_ceiling_percent: Figure = Field(
        description="The maximum loan to value ratio the policy permits, as a whole "
        "percent, e.g. 55.0 for 'maintain the LVR at a level below 55%'. This is the "
        "scheme's OWN policy limit, which may be tighter than the bank covenant."
    )
    icr_floor: Figure = Field(
        description="The minimum interest cover ratio the policy requires, as a multiple, "
        "e.g. 2.0 for 'not less than 2 times'. Written as words ('two times') as often as "
        "digits -- record 2.0 either way."
    )
    hedging_minimum_percent: Figure = Field(
        description="The minimum share of debt the policy requires to be hedged or fixed, "
        "as a whole percent, e.g. 50.0 for 'a minimum 50%'. Null if the document states no "
        "minimum."
    )
    debt_policy_notes: TextFigure = Field(
        description="Anything else the leverage or hedging policy commits to that the "
        "figures above do not capture -- amortisation, refinancing approach, remedies if a "
        "limit is exceeded. Quote or closely paraphrase."
    )


class ConductPolicy(BaseModel):
    """What the manager may charge, and when they may stop paying investors."""

    distribution_suspension_triggers: TextFigure = Field(
        description="The circumstances in which the manager may reduce or withhold "
        "distributions, quoted or closely paraphrased. These are the manager's own stated "
        "escape hatches and are worth knowing before they are used."
    )
    fee_entitlements: TextFigure = Field(
        description="What the SIPO says the manager is entitled to charge, and on what "
        "basis. Null if the SIPO does not address fees."
    )
    capex_policy: TextFigure = Field(
        description="What the document commits to on capital expenditure and maintaining "
        "the asset."
    )


class BaselinePolicy(BaseModel):
    """One scheme's stated policy, against which its reports can be judged."""

    identity: PolicyIdentity
    returns: ReturnTargets
    debt: DebtPolicy
    conduct: ConductPolicy

    extraction_notes: str | None = Field(
        description="Anything ambiguous or conditional: limits that differ by asset, "
        "policies stated as subject to change, or figures given more than once on "
        "different bases. Null if nothing of the sort."
    )


POLICY_SYSTEM_PROMPT = """\
You extract a New Zealand managed investment scheme's Statement of Investment Policy and
Objectives (SIPO) into a fixed schema.

A SIPO records what the manager promised. Those promises are later compared against what
the scheme actually reported, so a wrong figure here produces a false accusation or a false
reassurance. Both are worse than a null.

Rules:

1. Record only what the document states. Never infer a limit from an industry norm, and
   never carry a figure across from another scheme.

2. When the document does not state something, set its `value` to null, along with its
   `page` and `source_text`. Never omit a key, and never substitute zero.

3. Give the 1-based page number and a short verbatim quote for every figure you record.
   If you cannot point at the text, you have not found it.

4. Percentages are whole numbers (55.0 means 55%). Ratios are multiples (2.0 means two
   times). These documents write ratios as words as often as digits - "not less than two
   times" is 2.0.

5. Distinguish the scheme's OWN policy limit from any bank covenant it mentions. Record
   the scheme's policy limit; if a bank covenant is quoted as a separate and different
   figure, say so in extraction_notes.

6. Note the basis of the cash return exactly. A return on the investor's original equity is
   a different measure from one on current value, and confusing them makes every later
   comparison wrong.
"""


def build_policy_prompt(scheme_name: str | None = None) -> str:
    target = (
        f"This SIPO is expected to relate to {scheme_name}. If the document is plainly for "
        "a different scheme, extract what the document says and note the mismatch.\n\n"
        if scheme_name else ""
    )
    return (
        f"{target}Extract this SIPO into the schema. Record each field with its page "
        "number and a verbatim supporting quote, using null for anything the document does "
        "not state."
    )


class PolicyError(Exception):
    """Raised when a policy document could not be extracted."""


def extract_policy(pdf_bytes: bytes, api_key: str | None = None,
                   scheme_name: str | None = None,
                   model: str = DEFAULT_MODEL) -> BaselinePolicy:
    """Extract one SIPO. Single pass -- these documents are short."""
    client = (
        anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        if api_key else anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    )
    encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=POLICY_SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=BaselinePolicy,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf",
                                "data": encoded}},
                    {"type": "text", "text": build_policy_prompt(scheme_name)},
                ],
            }],
        )
    except anthropic.APIStatusError as e:
        raise PolicyError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise PolicyError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise PolicyError("The model declined to process this document.")
    policy = response.parsed_output
    if policy is None:
        raise PolicyError("The model returned no structured output.")
    return policy


def to_baseline_row(policy: BaselinePolicy) -> dict:
    """Map an extracted policy onto `Syndicate_Baseline` column names.

    Only the columns the SIPO actually speaks to; everything else is left alone
    so this never blanks a value someone entered by hand.
    """
    row = {}
    rate = policy.returns.minimum_cash_return_percent.value
    if rate is not None:
        row["im_forecast_distribution_rate"] = rate
        row["im_forecast_distribution_unit"] = "percent_per_annum_on_subscription_price"
    if policy.debt.lvr_ceiling_percent.value is not None:
        row["lvr_covenant_threshold"] = policy.debt.lvr_ceiling_percent.value
    if policy.debt.icr_floor.value is not None:
        row["icr_covenant_threshold"] = policy.debt.icr_floor.value
    if policy.identity.sipo_date.value:
        row["im_date"] = policy.identity.sipo_date.value

    parts = []
    for label, value in (
        ("min cash return", policy.returns.minimum_cash_return_percent.value),
        ("occupancy floor", policy.returns.occupancy_floor_percent.value),
        ("LVR ceiling", policy.debt.lvr_ceiling_percent.value),
        ("ICR floor", policy.debt.icr_floor.value),
        ("NTA floor", policy.returns.nta_floor_percent.value),
        ("hedging minimum", policy.debt.hedging_minimum_percent.value),
    ):
        if value is not None:
            parts.append(f"{label} {value:g}")
    basis = policy.returns.cash_return_basis.value
    if basis:
        parts.append(f"return basis: {basis[:80]}")
    if parts:
        row["trust_deed_notes"] = "SIPO: " + "; ".join(parts)
    return row
