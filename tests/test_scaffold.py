"""Tests for the baseline scaffolding helpers.

The script is a bootstrap, but it will be re-run whenever syndicates are added,
and a generated id that collapses two syndicates together is expensive to
discover later.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scaffold_baseline import (  # noqa: E402
    comparison_words,
    loosely_equal,
    make_aliases,
    make_id,
)


class TestIdGeneration:
    def test_manager_is_part_of_the_id(self):
        """Stripping it collapsed two different Industrial funds onto one code."""
        taken = set()
        a = make_id("Centuria Industrial Fund", "Centuria", taken)
        b = make_id("Jasper Industrial Income Plus Fund", "Jasper", taken)
        assert a != b
        assert a.startswith("CENT") and b.startswith("JASP")

    def test_single_letter_distinguisher_is_kept(self):
        """Building A and Building B differ by one letter and nothing else."""
        taken = set()
        a = make_id("Building A Graham Street Limited Partnership", "Centuria", taken)
        b = make_id("Building B Graham Street Limited Partnership", "Centuria", taken)
        assert a.endswith("A") and b.endswith("B")
        assert a != b

    def test_trailing_number_distinguisher_is_kept(self):
        taken = set()
        a = make_id("Centuria Govt Income 1", "Centuria", taken)
        b = make_id("Centuria Govt Income 2", "Centuria", taken)
        assert a.endswith("1") and b.endswith("2")

    def test_ids_never_collide(self):
        taken = set()
        ids = [make_id(n, "Centuria", taken) for n in
               ("Same Name Trust", "Same Name Trust", "Same Name Trust")]
        assert len(set(ids)) == 3

    def test_initials_are_not_used_as_the_whole_body(self):
        """'E+O Heathcare Fund' must not become ESKI-EO."""
        code = make_id("E+O Heathcare Fund", "Eskine & Owen", set())
        assert "HEATHCARE" in code


class TestAliases:
    def test_parenthetical_becomes_an_alias(self):
        aliases = make_aliases({"Centuria Airpark Nominees (Bendon)"})
        assert "Bendon" in aliases
        assert "Centuria Airpark Nominees" in aliases

    def test_legal_suffix_variant_becomes_an_alias(self):
        assert "Centuria Penrose" in make_aliases({"Centuria Penrose Ltd"})


class TestLooseMatching:
    def test_abbreviation_is_expanded(self):
        assert "street" in comparison_words('Graham St "B"')

    def test_plural_and_truncation_match_loosely(self):
        assert loosely_equal("william", "williams")
        assert loosely_equal("street", "street")

    def test_short_words_do_not_match_loosely(self):
        """Otherwise 'st' would match 'stgeorge' and merge unrelated syndicates."""
        assert not loosely_equal("st", "stgeorge")

    def test_different_words_do_not_match(self):
        assert not loosely_equal("penrose", "grenfell")
