"""Cross-check the stored extractions with a second model.

Re-extracts the same PDFs with a different model and diffs the result against
what is already stored in `Syndicate_Periods`. Where two models independently
agree on a figure, you can put it to a manager with confidence. Where they
disagree, that figure needs a human eye before it is used as evidence.

**This never overwrites stored data.** It reports; you decide.

Two habits are built in, both learned the expensive way:

- Every extraction is written to disk the moment it returns, so a later failure
  never forces a re-run. That matters more here: a second-model pass is paid for
  out of finite credits.
- Figures are compared with a tolerance, because 46.52 and 46.5 are the same
  number reported to different precision, and flagging that as a disagreement
  would bury the real ones.

    python scripts/crosscheck_fable.py                 # preview what it will do
    python scripts/crosscheck_fable.py --run           # run it
    python scripts/crosscheck_fable.py --run --only SGB
"""

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, ".")

import toml  # noqa: E402

from modules import sheets, storage  # noqa: E402
from modules.extraction import extract_report  # noqa: E402
from modules.narrative import number_tokens  # noqa: E402
from modules.schema import SyndicateReport, iter_figures  # noqa: E402

CROSSCHECK_MODEL = "claude-fable-5"

#: Where the downloaded reports live, and where second-model output is cached.
REPORT_DIRS = [
    r"C:\Users\Reforged\Downloads\register_reports",
    r"C:\Users\Reforged\Downloads",
]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "crosscheck_cache")

#: Two figures this far apart in relative terms are the same number reported to
#: different precision, not a disagreement.
TOLERANCE = 0.01

#: Narrative fields, where the substance is the numbers and the sentence around
#: them is the model's own words. Two models phrase these differently every time,
#: so comparing them as strings flags them on every run for every syndicate and
#: buries the real disagreements -- the same noise TOLERANCE exists to prevent.
#:
#: `entity_name`, `manager_name` and `report_type` are text too and are
#: deliberately NOT here: they identify the document. A mismatch there means the
#: wrong report or the wrong syndicate, which must stay loud.
PROSE_FIGURES = {"management_fee_escalation_basis", "related_party_transactions"}


def find_pdf(filename: str) -> str | None:
    for directory in REPORT_DIRS:
        candidate = os.path.join(directory, filename)
        if os.path.exists(candidate):
            return candidate
    # Fall back to a loose match, since stored filenames can drift.
    stem = os.path.splitext(filename)[0][:18]
    for directory in REPORT_DIRS:
        for path in glob.glob(os.path.join(directory, "*.pdf")):
            if os.path.basename(path).startswith(stem):
                return path
    return None


def agrees(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == 0 or b == 0:
            return a == b
        return abs(a - b) / max(abs(a), abs(b)) <= TOLERANCE
    return str(a).strip().lower() == str(b).strip().lower()


def compare_prose(a, b) -> str:
    """Compare two narrative statements.

    Returns "agree", "wording" (same numbers, different sentence),
    "second_omits" / "stored_omits" (one recorded fewer figures than the
    other) or "differ" (they genuinely conflict).

    The numbers carry the meaning -- the fee escalator, the related-party
    totals -- so those are compared exactly. Differing prose around identical
    numbers is two models writing one fact two ways: reported, but not a
    disagreement. One model stating something the other left null IS one, and
    is the case worth catching: a disclosure that one model found and the
    other missed.
    """
    if a is None and b is None:
        return "agree"
    if a is None or b is None:
        return "differ"
    x, y = set(number_tokens(str(a))), set(number_tokens(str(b)))
    if x != y:
        # A subset is one model recording less of a long note, not the two
        # contradicting each other. Kept separate because a contradiction needs
        # checking today and a missing prior-year comparative does not.
        if y < x:
            return "second_omits"
        if x < y:
            return "stored_omits"
        return "differ"
    return "agree" if str(a).strip() == str(b).strip() else "wording"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="actually call the API")
    parser.add_argument("--only", help="one syndicate_id")
    parser.add_argument("--model", default=CROSSCHECK_MODEL)
    args = parser.parse_args()

    label = args.model.split("-")[1]
    os.makedirs(CACHE, exist_ok=True)
    key = toml.load(".streamlit/secrets.toml")["ANTHROPIC_API_KEY"]
    spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    stored = spreadsheet.worksheet(storage.PERIODS_WORKSHEET).get_all_records()

    jobs = []
    for row in stored:
        sid = str(row.get("syndicate_id") or "")
        if args.only and sid != args.only:
            continue
        pdf = find_pdf(str(row.get("source_filename") or ""))
        jobs.append((sid, row, pdf))

    print(f"{len(jobs)} stored row(s); model {args.model}\n")
    for sid, _row, pdf in jobs:
        print(f"  {sid:<20} {'OK  ' if pdf else 'NO PDF'} {os.path.basename(pdf) if pdf else ''}")

    runnable = [j for j in jobs if j[2]]
    print(f"\n{len(runnable)} can be cross-checked. Fable is $10/$50 per MTok, "
          f"roughly ${len(runnable) * 1.6:.0f} for this set.")
    if not args.run:
        print("\nPreview only. Re-run with --run to call the API.")
        return

    disagreements = 0
    for sid, row, pdf in runnable:
        cached = os.path.join(CACHE, f"{sid}.json")
        if os.path.exists(cached):
            other = SyndicateReport.model_validate(json.load(open(cached, encoding="utf-8")))
            note = "(cached)"
        else:
            try:
                other = extract_report(pdf_bytes=open(pdf, "rb").read(), api_key=key,
                                       model=args.model)
            except Exception as e:  # noqa: BLE001 - one failure must not stop the rest
                print(f"\n=== {sid}: FAILED {str(e)[:110]}")
                continue
            # Write before comparing: a crash here must not cost another call.
            open(cached, "w", encoding="utf-8").write(other.model_dump_json(indent=2))
            note = ""
            time.sleep(2)

        original = SyndicateReport.model_validate(json.loads(row["raw_json"]))
        first = dict(iter_figures(original))
        second = dict(iter_figures(other))

        diffs = []
        wording = []
        for name in first:
            a, b = first[name].value, second[name].value
            if name in PROSE_FIGURES:
                verdict = compare_prose(a, b)
                if verdict == "differ":
                    diffs.append((name, a, b, first[name].page, second[name].page))
                elif verdict != "agree":
                    missing = {
                        "wording": "same numbers, different wording",
                        "second_omits": f"{label} omits "
                                        f"{len(set(number_tokens(str(a))) - set(number_tokens(str(b))))}"
                                        " figure(s) the stored value carries",
                        "stored_omits": f"{label} carries "
                                        f"{len(set(number_tokens(str(b))) - set(number_tokens(str(a))))}"
                                        " figure(s) the stored value omits",
                    }[verdict]
                    wording.append((name, missing, first[name].page, second[name].page))
            elif not agrees(a, b):
                diffs.append((name, a, b, first[name].page, second[name].page))

        checked = len(first)
        print(f"\n=== {sid} {note}  {checked - len(diffs)}/{checked} agree")
        for name, a, b, pa, pb in diffs:
            disagreements += 1
            print(f"    {name:<34} stored {str(a)[:60]:<62}(p{pa})")
            print(f"    {'':<34} {label:<6} {str(b)[:60]:<62}(p{pb})")
        for name, detail, pa, pb in wording:
            print(f"    {name:<34} {detail} (p{pa}/p{pb})")

    print(f"\n{disagreements} figure(s) disagree across the set - check those by hand.")
    print(f"Second-model output cached in {os.path.relpath(CACHE)}; nothing was overwritten.")


if __name__ == "__main__":
    main()
