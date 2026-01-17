from pathlib import Path

import pandas as pd
import pytest

from src.services.market_data import parse_google_finance_html, parse_nzx_html, parse_nzx_instrument_html
from src.services.return_ladder_dcf import (
    DCFInputs,
    build_dcf,
    build_summary_row,
    coerce_inputs_df,
    validate_rows,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_google_finance_html():
    html = (FIXTURES / "google_finance_sample.html").read_text(encoding="utf-8")
    assert parse_google_finance_html(html) == 123.45


def test_parse_nzx_html():
    html = (FIXTURES / "nzx_sample.html").read_text(encoding="utf-8")
    assert parse_nzx_html(html) == 5.67


def test_parse_nzx_securities_issued():
    html = (FIXTURES / "nzx_sample.html").read_text(encoding="utf-8")
    data = parse_nzx_instrument_html(html)
    assert data["shares_outstanding"] == 1234567890.0


def test_dcf_math_sanity():
    inputs = DCFInputs(
        ticker="TEST",
        market="US",
        current_price=100.0,
        shares_out=10.0,
        net_cash=0.0,
        fcf0=100.0,
        years=2,
        exit_multiple=10.0,
        growth_rate=0.0,
    )
    result = build_dcf(inputs, [0.10])
    fv = result.fair_values[0.10]
    assert fv == pytest.approx(100.0, rel=1e-3)


def test_summary_zone():
    inputs = DCFInputs(
        ticker="TEST",
        market="US",
        current_price=50.0,
        shares_out=10.0,
        net_cash=0.0,
        fcf0=100.0,
        years=2,
        exit_multiple=10.0,
        growth_rate=0.0,
    )
    result = build_dcf(inputs, [0.10])
    summary = build_summary_row(inputs, result, 0.10, zone_green=0.2, zone_red=-0.2)
    assert summary["Zone"] == "Green"


def test_coerce_and_validate_years():
    df = coerce_inputs_df(
        pd.DataFrame(
            [
                {
                    "ticker": "ABC",
                    "market": "US",
                    "years_to_exit": "5",
                    "exit_multiple": "20",
                    "growth_rate": "0.05",
                    "fcf_year0": "100",
                    "shares_out": "0",
                }
            ]
        )
    )
    rows = df.to_dict(orient="records")
    errors, warnings = validate_rows(rows)
    assert errors == []
    assert any("shares_out missing" in msg for msg in warnings)
