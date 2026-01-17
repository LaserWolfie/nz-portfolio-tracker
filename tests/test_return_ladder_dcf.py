from pathlib import Path

import pytest

from src.services.market_data import parse_google_finance_html, parse_nzx_html
from src.services.return_ladder_dcf import DCFInputs, build_dcf, build_summary_row


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_google_finance_html():
    html = (FIXTURES / "google_finance_sample.html").read_text(encoding="utf-8")
    assert parse_google_finance_html(html) == 123.45


def test_parse_nzx_html():
    html = (FIXTURES / "nzx_sample.html").read_text(encoding="utf-8")
    assert parse_nzx_html(html) == 5.67


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
