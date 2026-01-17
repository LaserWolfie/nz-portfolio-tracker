import pandas as pd

from src.services.signal_qualifier import apply_qualifies, qualifies_signal


def test_short_ticker_does_not_match_bare():
    row = pd.Series(
        {
            "Title": "Ebo Noah Fumes After Swap",
            "Summary": "",
            "Source": "",
        }
    )
    aliases = {"EBO", "EBOS", "NZX: EBO", "EBOS Group"}
    assert not qualifies_signal(row, "EBO", aliases)


def test_company_name_qualifies():
    row = pd.Series(
        {
            "Title": "EBOS Group expands distribution",
            "Summary": "",
            "Source": "",
        }
    )
    aliases = {"EBO", "EBOS", "NZX: EBO", "EBOS Group"}
    assert qualifies_signal(row, "EBO", aliases)


def test_apply_qualifies_sets_column():
    signals = pd.DataFrame(
        [
            {"Ticker": "EBO", "Title": "Ebo Noah Fumes...", "Summary": "", "Source": ""},
            {"Ticker": "EBO", "Title": "EBOS Group results", "Summary": "", "Source": ""},
        ]
    )
    alias_map = {"EBO": {"EBO", "EBOS", "NZX: EBO", "EBOS Group"}}
    out = apply_qualifies(signals, alias_map)
    assert out["QualifiesAuto"].tolist() == [False, True]
