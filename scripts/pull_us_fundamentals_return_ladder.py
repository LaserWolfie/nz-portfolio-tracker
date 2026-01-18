from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

import requests
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

APP_TICKERS_TAB = "APP_TICKERS"
APP_INPUTS_TAB = "APP_INPUTS"


def _normalize(text: str) -> str:
    return "".join(ch for ch in str(text or "").strip().lower() if ch.isalnum())


def _find_col_index(headers, target: str) -> int | None:
    target_norm = _normalize(target)
    for idx, header in enumerate(headers):
        if _normalize(header) == target_norm:
            return idx
    return None


def _get_sec_headers() -> dict:
    user_agent = str(st.secrets.get("sec_user_agent", "")).strip()
    if not user_agent:
        raise RuntimeError("sec_user_agent missing in secrets")
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


def _fetch_json(url: str, headers: dict) -> dict:
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _load_ticker_cik_map(headers: dict) -> dict[str, str]:
    data = _fetch_json(SEC_TICKERS_URL, headers)
    mapping = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).strip().upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        if ticker and cik:
            mapping[ticker] = cik
    return mapping


def _latest_annual_value(facts: dict, tag: str) -> float | None:
    units = (facts.get("us-gaap", {}).get(tag, {}) or {}).get("units", {})
    entries = []
    for unit_values in units.values():
        for item in unit_values:
            form = str(item.get("form", "")).upper()
            fp = str(item.get("fp", "")).upper()
            fy = item.get("fy")
            val = item.get("val")
            if form not in {"10-K", "10-K/A"} or fp != "FY":
                continue
            if val is None:
                continue
            entries.append((fy or 0, item.get("end", ""), val))
    if not entries:
        return None
    entries.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return float(entries[0][2])


def _first_available_value(facts: dict, tags: list[str]) -> float | None:
    for tag in tags:
        value = _latest_annual_value(facts, tag)
        if value is not None:
            return value
    return None


def _fetch_fundamentals(ticker: str, headers: dict, cik_map: dict[str, str]) -> dict:
    cik = cik_map.get(ticker)
    if not cik:
        raise RuntimeError(f"CIK not found for {ticker}")
    facts = _fetch_json(SEC_FACTS_URL.format(cik=cik), headers).get("facts", {})

    cash = _first_available_value(
        facts,
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
    )
    debt_current = _first_available_value(facts, ["DebtCurrent", "ShortTermBorrowings"])
    debt_long = _first_available_value(facts, ["LongTermDebt", "LongTermDebtNoncurrent"])
    total_debt = (debt_current or 0.0) + (debt_long or 0.0)

    cfo = _first_available_value(facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _first_available_value(facts, ["PaymentsToAcquirePropertyPlantAndEquipment"])
    if capex is not None:
        capex = abs(capex)

    net_cash = None if cash is None else (cash - total_debt)
    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - capex

    return {
        "net_cash_bn": None if net_cash is None else net_cash / 1e9,
        "fcf1_bn": None if fcf is None else fcf / 1e9,
    }


def main() -> int:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")

    headers = _get_sec_headers()
    cik_map = _load_ticker_cik_map(headers)

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    tickers_ws = ss.worksheet(APP_TICKERS_TAB)
    inputs_ws = ss.worksheet(APP_INPUTS_TAB)

    tickers_values = tickers_ws.get_all_values()
    if not tickers_values:
        raise RuntimeError("APP_TICKERS is empty.")
    tickers_header = tickers_values[0]
    ticker_col = _find_col_index(tickers_header, "Ticker")
    market_col = _find_col_index(tickers_header, "Market")
    active_col = _find_col_index(tickers_header, "Active")
    if ticker_col is None or market_col is None:
        raise RuntimeError("APP_TICKERS missing Ticker or Market columns.")

    active_us = []
    for row in tickers_values[1:]:
        if ticker_col >= len(row) or market_col >= len(row):
            continue
        ticker = str(row[ticker_col]).strip().upper()
        market = str(row[market_col]).strip().upper()
        active = str(row[active_col]).strip().upper() if active_col is not None and active_col < len(row) else "TRUE"
        if ticker and market == "US" and active in {"TRUE", "YES", "1"}:
            active_us.append(ticker)

    inputs_values = inputs_ws.get_all_values()
    if not inputs_values:
        raise RuntimeError("APP_INPUTS is empty.")
    inputs_header = inputs_values[0]
    inputs_ticker_col = _find_col_index(inputs_header, "Ticker")
    net_cash_col = _find_col_index(inputs_header, "Net cash/(debt) (bn)")
    fcf1_col = _find_col_index(inputs_header, "FCF1 (next-year, bn)")
    if inputs_ticker_col is None or net_cash_col is None or fcf1_col is None:
        raise RuntimeError("APP_INPUTS missing required columns.")

    updates = []
    for row_idx, row in enumerate(inputs_values[1:], start=2):
        if inputs_ticker_col >= len(row):
            continue
        ticker = str(row[inputs_ticker_col]).strip().upper()
        if ticker in active_us:
            data = _fetch_fundamentals(ticker, headers, cik_map)
            updates.append((row_idx, net_cash_col + 1, data.get("net_cash_bn")))
            updates.append((row_idx, fcf1_col + 1, data.get("fcf1_bn")))

    if updates:
        cell_list = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")

    print(f"Updated {len(active_us)} tickers in APP_INPUTS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
