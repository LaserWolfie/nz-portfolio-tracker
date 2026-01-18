from __future__ import annotations

from pathlib import Path
from typing import List
import re
import sys

import streamlit as st
from gspread.utils import rowcol_to_a1

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client


APP_INPUTS_TAB = "APP_INPUTS"
APP_TICKERS_TAB = "APP_TICKERS"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _find_col_index(headers: list[str], target: str) -> int | None:
    target_norm = _normalize(target)
    for idx, header in enumerate(headers):
        if _normalize(header) == target_norm:
            return idx
    return None


def _find_any_col_index(headers: list[str], targets: list[str]) -> int | None:
    for target in targets:
        idx = _find_col_index(headers, target)
        if idx is not None:
            return idx
    return None


def _col_letter(col_idx: int) -> str:
    return re.sub(r"\d", "", rowcol_to_a1(1, col_idx))


def _seed_app_inputs_formulas(inputs_ws):
    header = inputs_ws.row_values(1)
    if not header:
        raise RuntimeError("APP_INPUTS header row is missing.")

    row_idx = 2
    ticker_col = _find_col_index(header, "Ticker")
    market_col = _find_any_col_index(header, ["Market"])
    company_col = _find_col_index(header, "Company")
    ccy_col = _find_col_index(header, "CCY")
    price_col = _find_col_index(header, "Price")
    shares_col = _find_any_col_index(header, ["Shares (bn)", "Shares_bn"])
    g_col = _find_any_col_index(header, ["g (Y1-Y5)", "g_y1y5", "g_1_5"])
    n_col = _find_col_index(header, "N (yrs)")
    g_terminal_col = _find_col_index(header, "g terminal")
    net_cash_col = _find_any_col_index(header, ["Net cash/(debt) (bn)", "Net_cash_debt_bn", "NetCash_bn"])

    if ticker_col is None:
        raise RuntimeError("APP_INPUTS is missing the Ticker column.")

    ticker_ref = f"${_col_letter(ticker_col + 1)}{row_idx}"
    if market_col is not None:
        market_ref = f"${_col_letter(market_col + 1)}{row_idx}"
        market_formula = f'=IFERROR(VLOOKUP({ticker_ref}, APP_TICKERS!A:B, 2, FALSE), "")'
    else:
        market_ref = f"IFERROR(VLOOKUP({ticker_ref}, APP_TICKERS!A:B, 2, FALSE), \"\")"
        market_formula = None

    updates = []
    if market_col is not None:
        updates.append((row_idx, market_col + 1, market_formula))
    if company_col is not None:
        updates.append(
            (
                row_idx,
                company_col + 1,
                f'=IF({market_ref}="US", IFERROR(GOOGLEFINANCE({ticker_ref},"name"), ""), "")',
            )
        )
    if ccy_col is not None:
        updates.append(
            (
                row_idx,
                ccy_col + 1,
                f'=IF({market_ref}="NZ","NZD", IF({market_ref}="US","USD",""))',
            )
        )
    if price_col is not None:
        updates.append(
            (
                row_idx,
                price_col + 1,
                f'=IF({market_ref}="NZ", IFERROR(NZX_PRICE({ticker_ref}), ""), '
                f'IFERROR(GOOGLEFINANCE({ticker_ref},"price"), ""))',
            )
        )
    if shares_col is not None and price_col is not None:
        price_ref = f"${_col_letter(price_col + 1)}{row_idx}"
        updates.append(
            (
                row_idx,
                shares_col + 1,
                f'=IF({market_ref}="US", IFERROR(GOOGLEFINANCE({ticker_ref},"marketcap") / {price_ref} / 1e9, ""), "")',
            )
        )
    if g_col is not None:
        updates.append(
            (
                row_idx,
                g_col + 1,
                f'=IF({market_ref}="US", 0.06, IF({market_ref}="NZ", 0.03, ""))',
            )
        )
    if net_cash_col is not None:
        updates.append((row_idx, net_cash_col + 1, ""))
    if n_col is not None:
        updates.append((row_idx, n_col + 1, 5))
    if g_terminal_col is not None:
        updates.append((row_idx, g_terminal_col + 1, 0.03))

    if updates:
        cells = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cells, updates):
            cell.value = value
        inputs_ws.update_cells(cells, value_input_option="USER_ENTERED")


def main() -> int:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    inputs_ws = ss.worksheet(APP_INPUTS_TAB)

    _seed_app_inputs_formulas(inputs_ws)
    print("Seeded APP_INPUTS formulas in row 2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
