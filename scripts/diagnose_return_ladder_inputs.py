from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client
from src.services.return_ladder_app_inputs import APP_INPUTS_HEADERS, build_header_map


APP_INPUTS_TAB = "APP_INPUTS"

CANONICAL_HEADERS = APP_INPUTS_HEADERS
REQUIRED_FIELDS = [
    "Company",
    "Ticker",
    "Market",
    "CCY",
    "Price",
    "Shares_bn",
    "Net cash/(debt) (bn)",
    "FCF1 (next-year, bn)",
    "g (Y1-Y5)",
    "N (yrs)",
    "g_terminal",
]


def _is_blank(value) -> bool:
    return str(value or "").strip() == ""


def _get_cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _infer_market(ticker: str, ccy: str) -> str:
    upper = ticker.upper()
    if upper.startswith("NZ:") or upper.startswith("NZX:") or ccy.upper() == "NZD":
        return "NZ"
    return "US"


def main() -> int:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    ws = ss.worksheet(APP_INPUTS_TAB)
    values = ws.get_all_values()
    if not values:
        print("APP_INPUTS is empty.")
        return 1

    headers = values[0]
    header_map = build_header_map(headers)
    print("Detected headers:")
    for key in CANONICAL_HEADERS:
        idx = header_map.get(key)
        if idx is None:
            print(f"  {key}: MISSING")
        else:
            print(f"  {key}: column {idx + 1} ({headers[idx]})")
    print(f"Row count (including header): {len(values)}")

    ticker_idx = header_map.get("Ticker")
    if ticker_idx is None:
        print("Ticker column not found; cannot report per-ticker missing fields.")
        return 1

    print("\nPer-ticker missing fields:")
    warnings = []
    us_net_cash_missing = []
    nz_shares_missing = []
    for row in values[1:]:
        ticker = str(row[ticker_idx]).strip().upper() if ticker_idx < len(row) else ""
        if not ticker:
            continue
        market = _get_cell(row, header_map.get("Market"))
        ccy = _get_cell(row, header_map.get("CCY"))
        inferred_market = market or _infer_market(ticker, ccy)
        missing = []
        for name in REQUIRED_FIELDS:
            idx = header_map.get(name)
            if idx is None or idx >= len(row) or _is_blank(row[idx]):
                if name == "Net cash/(debt) (bn)" and inferred_market == "US":
                    us_net_cash_missing.append(ticker)
                    missing.append(name)
                elif name == "Shares_bn" and inferred_market == "NZ":
                    nz_shares_missing.append(ticker)
                    missing.append(name)
                elif name in {"Net cash/(debt) (bn)", "FCF1 (next-year, bn)"} and inferred_market == "NZ":
                    warnings.append(f"- {ticker}: missing {name}")
                else:
                    missing.append(name)
        if missing:
            print(f"- {ticker}: missing {', '.join(missing)}")
    if us_net_cash_missing:
        print("\nUS tickers missing Net cash/(debt) (bn):")
        for ticker in sorted(set(us_net_cash_missing)):
            print(f"- {ticker}")
    if nz_shares_missing:
        print("\nNZ tickers missing Shares_bn:")
        for ticker in sorted(set(nz_shares_missing)):
            print(f"- {ticker}")
    if warnings:
        print("\nPer-ticker warnings:")
        for entry in warnings:
            print(entry)

    print("\nRun:")
    print("  python scripts/diagnose_return_ladder_inputs.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
