"""One-off bootstrap: seed Syndicate_Baseline from Syndicate_Data.

`Syndicate_Data` holds one row per HOLDING (owner x syndicate), so the same
syndicate appears more than once when several family entities own units in it.
`Syndicate_Baseline` is one row per SYNDICATE, because LVR, cap rate and WALE
are properties of the syndicate rather than of who holds units. This script
deduplicates accordingly and records every owner against the single row.

It never merges two names on a guess. Names that merely look similar are
reported as "possible duplicates" for a human to confirm, because wrongly
merging two syndicates corrupts both histories -- and "Building A Graham
Street" and "Building B Graham Street" are genuinely different properties.

Run with --dry-run (the default) to preview. Pass --write to apply. Existing
rows are never modified or removed; only missing syndicates are appended.

    .venvapp/Scripts/python.exe scripts/scaffold_baseline.py
    .venvapp/Scripts/python.exe scripts/scaffold_baseline.py --write
"""

import argparse
import difflib
import re
import sys

sys.path.insert(0, ".")

from modules import sheets, storage  # noqa: E402

#: Words that carry no identifying information in an NZ syndicate name.
NOISE_WORDS = {
    "the", "limited", "ltd", "limited partnership", "lp", "trust", "fund",
    "nominees", "joint", "venture", "property", "properties", "partnership",
    "group", "nz", "n.z.", "new", "zealand", "income", "plus", "direct",
}

#: Manager names that prefix many syndicates and so make a poor id.
MANAGER_WORDS = {"centuria", "pmg", "oyster", "jasper", "merx", "augusta"}


def normalise(name: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    return " ".join(text.split())


#: Abbreviations that differ only in spelling between documents.
ABBREVIATIONS = {
    "st": "street", "rd": "road", "ave": "avenue", "dr": "drive",
    "govt": "government", "hlth": "health", "pde": "parade",
}


def significant_words(name: str) -> list[str]:
    return [w for w in normalise(name).split() if w not in NOISE_WORDS]


def comparison_words(name: str) -> list[str]:
    """Distinctive words, abbreviations expanded, for similarity comparison only."""
    return [ABBREVIATIONS.get(w, w)
            for w in significant_words(name) if w not in MANAGER_WORDS]


def loosely_equal(a: str, b: str) -> bool:
    """True for words that differ only by a plural or a truncation."""
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= 4 and long.startswith(short)


def make_id(name: str, manager: str, taken: set[str]) -> str:
    """A short, stable, human-readable code: MANAGER-DISTINCTIVE.

    The manager belongs in the id. Stripping it collapsed "Centuria Industrial
    Fund" and "Jasper Industrial Income Plus Fund" onto the same code, which is
    exactly the collision an id exists to prevent.
    """
    prefix = re.sub(r"[^A-Z]", "", str(manager).upper())[:4] or "SYND"

    tokens = [w for w in significant_words(name) if w not in MANAGER_WORDS]
    core = [w for w in tokens if len(w) > 1 and not w.isdigit()]
    # A lone letter or number is usually the ONLY thing separating two
    # syndicates -- Building A vs B, Govt Income 1 vs 2. Dropping it would leave
    # the ids distinguished solely by an arbitrary collision suffix, whose value
    # depends on row order and so is not stable.
    marks = [w for w in tokens if len(w) == 1 or w.isdigit()]

    if not core:
        core = tokens or ["x"]
    if len(marks) == 1:
        body = (core[0][:11] + marks[0]).upper()
    else:
        body = "".join(core[:2])[:12].upper()

    code = f"{prefix}-{body}"

    candidate, n = code, 2
    while candidate in taken:
        candidate = f"{code}{n}"
        n += 1
    taken.add(candidate)
    return candidate


def make_aliases(names: set[str]) -> list[str]:
    """Every spelling we have seen, plus a few plausible shortenings."""
    aliases = set()
    for name in names:
        aliases.add(name.strip())
        # Drop a trailing legal form and any parenthetical.
        aliases.add(re.sub(r"\s*\([^)]*\)", "", name).strip())
        aliases.add(re.sub(r"\b(Limited Partnership|Limited|Ltd|LP)\b\.?", "",
                           name, flags=re.I).strip())
        inner = re.findall(r"\(([^)]+)\)", name)
        aliases.update(i.strip() for i in inner)
    return sorted(a for a in aliases if a)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="apply instead of preview")
    args = parser.parse_args()

    spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    source = spreadsheet.worksheet(sheets.PROPERTY_WORKSHEET).get_all_records()
    baseline_ws = spreadsheet.worksheet(storage.BASELINE_WORKSHEET)
    existing = storage.load_baseline(baseline_ws)

    existing_keys = set()
    taken_ids = set()
    for row in existing:
        taken_ids.add(str(row.get("syndicate_id", "")))
        existing_keys.add(normalise(row.get("canonical_name", "")))
        for alias in str(row.get("aliases", "") or "").split(storage.ALIAS_SEPARATOR):
            if alias.strip():
                existing_keys.add(normalise(alias))

    # Collapse holdings into syndicates.
    syndicates: dict[str, dict] = {}
    for row in source:
        name = str(row.get("Entity_Name", "")).strip()
        if not name or "total" in name.lower():
            continue
        key = normalise(name)
        entry = syndicates.setdefault(
            key, {"names": set(), "owners": set(), "managers": set(), "sectors": set()}
        )
        entry["names"].add(name)
        for field, bucket in (("Owner_Entity", "owners"), ("Manager", "managers"),
                              ("Sector", "sectors")):
            value = str(row.get(field, "")).strip()
            if value:
                entry[field.split("_")[0].lower() + "s" if False else bucket].add(value)

    print(f"{len(source)} holding rows -> {len(syndicates)} distinct syndicates")
    print(f"{len(existing)} baseline row(s) already present\n")

    new_rows = []
    for key, entry in sorted(syndicates.items()):
        if key in existing_keys:
            print(f"  SKIP    {sorted(entry['names'])[0][:48]:<48} already in baseline")
            continue
        canonical = max(entry["names"], key=len)
        new_rows.append({
            "syndicate_id": make_id(canonical, sorted(entry["managers"])[0] if entry["managers"] else "", taken_ids),
            "canonical_name": canonical,
            "aliases": storage.ALIAS_SEPARATOR.join(
                a for a in make_aliases(entry["names"]) if normalise(a) != normalise(canonical)
            ),
            "owner_entity": storage.ALIAS_SEPARATOR.join(sorted(entry["owners"])),
            "manager_name": storage.ALIAS_SEPARATOR.join(sorted(entry["managers"])),
            "sector": storage.ALIAS_SEPARATOR.join(sorted(entry["sectors"])),
            "notes": "Scaffolded from Syndicate_Data; IM figures not yet entered.",
        })

    for row in new_rows:
        owners = row["owner_entity"].replace(storage.ALIAS_SEPARATOR, ", ")
        print(f"  NEW     {row['syndicate_id']:<14} {row['canonical_name'][:46]:<46} {owners}")

    # Flag names that look like the same syndicate spelled differently.
    print("\nPossible duplicates -- confirm by hand, do NOT assume:")
    keys = sorted(syndicates)
    found = False
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            words_a = comparison_words(a)
            words_b = comparison_words(b)
            # Compare only the distinctive words, so shared boilerplate like
            # "Nominees Joint Venture" does not make every pair look alike.
            # Match loosely, since "William St" and "Williams Street" are the
            # same place written two ways.
            shared = sum(1 for x in words_a if any(loosely_equal(x, y) for y in words_b))
            ratio = difflib.SequenceMatcher(None, " ".join(words_a), " ".join(words_b)).ratio()
            if shared >= 2 or ratio > 0.72:
                found = True
                print(f"  ?  {sorted(syndicates[a]['names'])[0][:44]:<44} <-> "
                      f"{sorted(syndicates[b]['names'])[0][:44]}")
    if not found:
        print("  (none)")

    if not args.write:
        print(f"\nDRY RUN. {len(new_rows)} row(s) would be appended. Re-run with --write.")
        return

    header = storage.read_header(baseline_ws)
    for row in new_rows:
        baseline_ws.append_row(storage.row_from_dict(header, row),
                               value_input_option="USER_ENTERED")
    print(f"\nAppended {len(new_rows)} baseline row(s).")


if __name__ == "__main__":
    main()
