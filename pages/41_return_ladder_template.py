from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import List

import pandas as pd
import streamlit as st
from gspread.utils import rowcol_to_a1

from src.data.sheets import get_gspread_client


st.set_page_config(page_title="Return Ladder (Template Viewer)", page_icon="\U0001F4C4", layout="wide")

st.title("Return Ladder (Template Viewer)")
st.caption("Template viewer with app-side DCF calculations and write-back to app tabs.")

APP_TICKERS_TAB = "APP_TICKERS"
APP_INPUTS_TAB = "APP_INPUTS"
APP_SOURCES_TAB = "APP_SOURCES"

APP_TICKERS_HEADERS = ["Ticker", "Market", "Active", "AddedAt"]
APP_INPUTS_HEADERS = [
    "Company",
    "Ticker",
    "Market",
    "CCY",
    "Price",
    "Shares (bn)",
    "Net cash/(debt) (bn)",
    "FCF1 (next-year, bn)",
    "g (Y1-Y5)",
    "N (yrs)",
    "g terminal",
    "Notes",
    "Links",
]
APP_SOURCES_HEADERS = ["Ticker", "Metric", "URL", "Notes", "UpdatedAt"]

REQUIRED_RETURNS = [0.08, 0.10, 0.15, 0.20]

refresh = st.button("Refresh")
if refresh:
    st.session_state["template_refresh_token"] = st.session_state.get("template_refresh_token", 0) + 1

refresh_token = st.session_state.get("template_refresh_token", 0)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _get_sheet_id() -> str:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")
    return sheet_id


def _ensure_headers(ws, headers: List[str], assume_empty: bool = False):
    if assume_empty:
        ws.update(range_name="A1", values=[headers])
        ws.freeze(rows=1)
        return
    row1 = ws.row_values(1)
    if not row1:
        ws.update(range_name="A1", values=[headers])
        ws.freeze(rows=1)


def _ensure_app_tabs(sheet_id: str, ensure_headers: bool = False):
    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    worksheets = {ws.title: ws for ws in ss.worksheets()}

    if APP_TICKERS_TAB in worksheets:
        tickers_ws = worksheets[APP_TICKERS_TAB]
    else:
        tickers_ws = ss.add_worksheet(title=APP_TICKERS_TAB, rows=200, cols=len(APP_TICKERS_HEADERS) + 5)
        _ensure_headers(tickers_ws, APP_TICKERS_HEADERS, assume_empty=True)
    if ensure_headers:
        _ensure_headers(tickers_ws, APP_TICKERS_HEADERS)

    if APP_INPUTS_TAB in worksheets:
        inputs_ws = worksheets[APP_INPUTS_TAB]
    else:
        inputs_ws = ss.add_worksheet(title=APP_INPUTS_TAB, rows=200, cols=len(APP_INPUTS_HEADERS) + 5)
        _ensure_headers(inputs_ws, APP_INPUTS_HEADERS, assume_empty=True)
    if ensure_headers:
        _ensure_headers(inputs_ws, APP_INPUTS_HEADERS)

    if APP_SOURCES_TAB in worksheets:
        sources_ws = worksheets[APP_SOURCES_TAB]
    else:
        sources_ws = ss.add_worksheet(title=APP_SOURCES_TAB, rows=200, cols=len(APP_SOURCES_HEADERS) + 5)
        _ensure_headers(sources_ws, APP_SOURCES_HEADERS, assume_empty=True)
    if ensure_headers:
        _ensure_headers(sources_ws, APP_SOURCES_HEADERS)

    return ss, tickers_ws, inputs_ws, sources_ws


def _read_table(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = values[0]
    rows = [row for row in values[1:] if any(str(cell).strip() for cell in row)]
    width = len(headers)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    return pd.DataFrame(normalized_rows, columns=headers)


def _map_inputs_df(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {
        "company": "Company",
        "ticker": "Ticker",
        "market": "Market",
        "ccy": "CCY",
        "price": "Price",
        "sharesbn": "Shares (bn)",
        "netcashdebtbn": "Net cash/(debt) (bn)",
        "netcashbn": "Net cash/(debt) (bn)",
        "fcf1nextyearbn": "FCF1 (next-year, bn)",
        "fcf1bn": "FCF1 (next-year, bn)",
        "gy1y5": "g (Y1-Y5)",
        "g15": "g (Y1-Y5)",
        "nyrs": "N (yrs)",
        "nyears": "N (yrs)",
        "n": "N (yrs)",
        "gterminal": "g terminal",
        "notes": "Notes",
        "links": "Links",
    }
    columns = {}
    for col in df.columns:
        normalized = _normalize(col)
        mapped = mapping.get(normalized)
        if mapped and mapped not in columns.values():
            columns[col] = mapped
    if not columns:
        return pd.DataFrame(columns=APP_INPUTS_HEADERS)
    mapped_df = df.rename(columns=columns)
    for col in APP_INPUTS_HEADERS:
        if col not in mapped_df.columns:
            mapped_df[col] = ""
    return mapped_df[APP_INPUTS_HEADERS]


def _map_sources_df(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {
        "ticker": "Ticker",
        "metric": "Metric",
        "field": "Metric",
        "url": "URL",
        "notes": "Notes",
        "updatedat": "UpdatedAt",
        "updatedatutc": "UpdatedAt",
    }
    columns = {}
    for col in df.columns:
        normalized = _normalize(col)
        mapped = mapping.get(normalized)
        if mapped and mapped not in columns.values():
            columns[col] = mapped
    if not columns:
        return pd.DataFrame(columns=APP_SOURCES_HEADERS)
    mapped_df = df.rename(columns=columns)
    for col in APP_SOURCES_HEADERS:
        if col not in mapped_df.columns:
            mapped_df[col] = ""
    return mapped_df[APP_SOURCES_HEADERS]


def _parse_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _parse_rate(value) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return 0.0
    return number / 100.0 if is_percent else number


def _parse_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def _safe_float(value, default=0.0) -> float:
    parsed = _parse_float(value)
    return parsed if parsed is not None else default


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


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _col_letter(col_idx: int) -> str:
    return re.sub(r"\d", "", rowcol_to_a1(1, col_idx))


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
    import requests

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _load_ticker_cik_map(headers: dict) -> dict[str, str]:
    data = _fetch_json("https://www.sec.gov/files/company_tickers.json", headers)
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


def _fetch_us_fundamentals(ticker: str, headers: dict, cik_map: dict[str, str]) -> dict:
    cik = cik_map.get(ticker)
    if not cik:
        raise RuntimeError(f"CIK not found for {ticker}")
    facts = _fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers).get("facts", {})

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


def _last_non_empty_row(values: list[list[str]], col_idx: int | None) -> int:
    last_row = 1
    for row_idx, row in enumerate(values[1:], start=2):
        if col_idx is not None and col_idx < len(row):
            if str(row[col_idx]).strip():
                last_row = row_idx
        elif any(str(cell).strip() for cell in row):
            last_row = row_idx
    return last_row


def _append_ticker_row(tickers_ws, ticker: str, market: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tickers_ws.append_row([ticker, market, "TRUE", timestamp], value_input_option="USER_ENTERED")


def _seed_sources(sources_ws, ticker: str, market: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    if market == "US":
        rows.extend(
            [
                [
                    ticker,
                    "SEC company search",
                    f"https://www.sec.gov/edgar/search/#/q={ticker}",
                    "auto-seeded",
                    timestamp,
                ],
                [
                    ticker,
                    "Latest filings",
                    f"https://www.sec.gov/edgar/search/#/q={ticker}&category=custom&forms=10-K,10-Q,8-K",
                    "auto-seeded",
                    timestamp,
                ],
                [
                    ticker,
                    "Macrotrends FCF/Cashflow",
                    f"https://www.macrotrends.net/stocks/charts/{ticker}/",
                    "auto-seeded",
                    timestamp,
                ],
            ]
        )
    else:
        code = ticker.replace("NZX:", "")
        rows.extend(
            [
                [
                    ticker,
                    "NZX instrument",
                    f"https://www.nzx.com/instruments/{code}",
                    "auto-seeded",
                    timestamp,
                ],
                [
                    ticker,
                    "NZX announcements",
                    f"https://www.nzx.com/companies/{code}/announcements",
                    "auto-seeded",
                    timestamp,
                ],
            ]
        )
    if rows:
        sources_ws.append_rows(rows, value_input_option="USER_ENTERED")


def _copy_formula_row(inputs_ws, target_row: int, header: list[str], ticker: str, market: str):
    sheet_id = inputs_ws._properties.get("sheetId")
    if sheet_id is None:
        raise RuntimeError("Could not determine sheetId for APP_INPUTS")

    col_count = max(len(header), inputs_ws.col_count)

    inputs_ws.spreadsheet.batch_update(
        {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": 2,
                            "startColumnIndex": 0,
                            "endColumnIndex": col_count,
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": target_row - 1,
                            "endRowIndex": target_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": col_count,
                        },
                        "pasteType": "PASTE_FORMULA",
                        "pasteOrientation": "NORMAL",
                    }
                }
            ]
        }
    )

    updates = []
    ticker_col = _find_col_index(header, "Ticker")
    market_col = _find_col_index(header, "Market")
    company_col = _find_col_index(header, "Company")
    notes_col = _find_col_index(header, "Notes")
    links_col = _find_col_index(header, "Links")

    if ticker_col is not None:
        updates.append((target_row, ticker_col + 1, ticker))
    if market_col is not None:
        updates.append((target_row, market_col + 1, market))
    if company_col is not None:
        updates.append((target_row, company_col + 1, ""))
    if notes_col is not None:
        updates.append((target_row, notes_col + 1, ""))
    if links_col is not None:
        updates.append((target_row, links_col + 1, ""))

    if updates:
        cell_list = [inputs_ws.cell(row, col) for row, col, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")


def _append_inputs_row(inputs_ws, ticker: str, market: str):
    values = inputs_ws.get_all_values()
    if not values or len(values) < 1:
        raise RuntimeError("APP_INPUTS must have a header row.")
    header = values[0]
    template_row = values[1] if len(values) > 1 else []
    has_template = any(str(cell).strip() for cell in template_row)

    ticker_col = _find_col_index(header, "Ticker")
    last_row = _last_non_empty_row(values, ticker_col)
    target_row = last_row + 1

    if has_template:
        if target_row <= inputs_ws.row_count:
            if len(values) >= target_row and any(str(cell).strip() for cell in values[target_row - 1]):
                inputs_ws.spreadsheet.batch_update(
                    {
                        "requests": [
                            {
                                "insertDimension": {
                                    "range": {
                                        "sheetId": inputs_ws._properties.get("sheetId"),
                                        "dimension": "ROWS",
                                        "startIndex": target_row - 1,
                                        "endIndex": target_row,
                                    },
                                    "inheritFromBefore": False,
                                }
                            }
                        ]
                    }
                )
        else:
            inputs_ws.add_rows(target_row - inputs_ws.row_count)

        _copy_formula_row(inputs_ws, target_row, header, ticker, market)
        return

    row_values = [""] * len(header)
    if ticker_col is not None:
        row_values[ticker_col] = ticker
    market_col = _find_col_index(header, "Market")
    if market_col is not None:
        row_values[market_col] = market
    inputs_ws.append_row(row_values, value_input_option="USER_ENTERED")


def _set_ticker_active(tickers_ws, ticker: str, active: bool):
    values = tickers_ws.get_all_values()
    if not values:
        return
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    active_col = _find_col_index(header, "Active")
    if ticker_col is None:
        return
    if active_col is None:
        header = header + ["Active"]
        tickers_ws.update(range_name="A1", values=[header])
        active_col = len(header) - 1
    target_key = _ticker_key(ticker)
    target_norm = _normalize_ticker(ticker)
    updates = []
    for row_idx, row in enumerate(values[1:], start=2):
        if ticker_col >= len(row):
            continue
        cell_value = str(row[ticker_col])
        row_ticker = _ticker_key(cell_value)
        row_norm = _normalize_ticker(cell_value)
        if row_ticker == target_key or row_norm == target_norm or target_norm in row_norm:
            updates.append((row_idx, active_col + 1, "TRUE" if active else "FALSE"))
    if updates:
        cell_list = [tickers_ws.cell(row, col) for row, col, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        tickers_ws.update_cells(cell_list, value_input_option="USER_ENTERED")


def _dedupe_tickers_ws(tickers_ws) -> int:
    values = tickers_ws.get_all_values()
    if not values:
        return 0
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    market_col = _find_col_index(header, "Market")
    added_col = _find_col_index(header, "AddedAt")
    if ticker_col is None:
        return 0
    keep_map: dict[tuple[str, str], tuple[int, datetime | None]] = {}
    delete_rows = []
    for row_idx, row in enumerate(values[1:], start=2):
        ticker = _ticker_key(row[ticker_col]) if ticker_col < len(row) else ""
        market = ""
        if market_col is not None and market_col < len(row):
            market = str(row[market_col]).strip().upper()
        if not ticker:
            continue
        key = (ticker, market)
        timestamp = None
        if added_col is not None and added_col < len(row):
            timestamp = _parse_timestamp(row[added_col])
        if key not in keep_map:
            keep_map[key] = (row_idx, timestamp)
            continue
        keep_idx, keep_ts = keep_map[key]
        if timestamp and (keep_ts is None or timestamp > keep_ts):
            delete_rows.append(keep_idx)
            keep_map[key] = (row_idx, timestamp)
        else:
            delete_rows.append(row_idx)
    for row_idx in sorted(set(delete_rows), reverse=True):
        tickers_ws.delete_rows(row_idx)
    return len(set(delete_rows))


def _dedupe_inputs_ws(inputs_ws) -> int:
    values = inputs_ws.get_all_values()
    if not values:
        return 0
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    market_col = _find_col_index(header, "Market")
    if ticker_col is None:
        return 0
    keep_map: dict[tuple[str, str], tuple[int, int]] = {}
    delete_rows = []
    for row_idx, row in enumerate(values[1:], start=2):
        if ticker_col >= len(row):
            continue
        ticker = _ticker_key(row[ticker_col])
        market = ""
        if market_col is not None and market_col < len(row):
            market = str(row[market_col]).strip().upper()
        if not ticker:
            continue
        key = (ticker, market)
        score = sum(1 for cell in row if str(cell).strip())
        if key not in keep_map:
            keep_map[key] = (row_idx, score)
            continue
        keep_idx, keep_score = keep_map[key]
        if score > keep_score:
            delete_rows.append(keep_idx)
            keep_map[key] = (row_idx, score)
        else:
            delete_rows.append(row_idx)
    for row_idx in sorted(set(delete_rows), reverse=True):
        inputs_ws.delete_rows(row_idx)
    return len(set(delete_rows))


def _dedupe_sources_ws(sources_ws) -> int:
    values = sources_ws.get_all_values()
    if not values:
        return 0
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    metric_col = _find_any_col_index(header, ["Metric", "Field"])
    url_col = _find_col_index(header, "URL")
    updated_col = _find_any_col_index(header, ["UpdatedAt", "UpdatedAt_UTC"])
    if ticker_col is None or metric_col is None or url_col is None:
        return 0

    def _normalize_url(value: str) -> str:
        text = str(value or "").strip().lower()
        if text.endswith("/"):
            text = text[:-1]
        return text

    seen: dict[tuple[str, str, str], tuple[int, datetime | None]] = {}
    delete_rows = []
    for row_idx, row in enumerate(values[1:], start=2):
        if ticker_col >= len(row):
            continue
        ticker = _ticker_key(row[ticker_col])
        metric = str(row[metric_col]).strip().lower() if metric_col < len(row) else ""
        url = _normalize_url(row[url_col]) if url_col < len(row) else ""
        if not ticker:
            continue
        key = (ticker, metric, url or "__blank__")
        timestamp = None
        if updated_col is not None and updated_col < len(row):
            timestamp = _parse_timestamp(row[updated_col])
        if key not in seen:
            seen[key] = (row_idx, timestamp)
            continue
        keep_idx, keep_ts = seen[key]
        if timestamp and (keep_ts is None or timestamp > keep_ts):
            delete_rows.append(keep_idx)
            seen[key] = (row_idx, timestamp)
        else:
            delete_rows.append(row_idx)
    for row_idx in sorted(set(delete_rows), reverse=True):
        sources_ws.delete_rows(row_idx)
    return len(set(delete_rows))


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


def _autofill_us_fundamentals(tickers_ws, inputs_ws, cache: dict) -> list[dict]:
    tickers_values = tickers_ws.get_all_values()
    if not tickers_values:
        return [{"status": "error", "ticker": "", "message": "APP_TICKERS is empty."}]
    tickers_header = tickers_values[0]
    ticker_col = _find_col_index(tickers_header, "Ticker")
    market_col = _find_col_index(tickers_header, "Market")
    active_col = _find_col_index(tickers_header, "Active")
    if ticker_col is None or market_col is None:
        return [{"status": "error", "ticker": "", "message": "APP_TICKERS missing Ticker/Market columns."}]

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
        return [{"status": "error", "ticker": "", "message": "APP_INPUTS is empty."}]
    inputs_header = inputs_values[0]
    inputs_ticker_col = _find_col_index(inputs_header, "Ticker")
    net_cash_col = _find_col_index(inputs_header, "Net cash/(debt) (bn)")
    fcf1_col = _find_col_index(inputs_header, "FCF1 (next-year, bn)")
    if inputs_ticker_col is None or net_cash_col is None or fcf1_col is None:
        return [{"status": "error", "ticker": "", "message": "APP_INPUTS missing required columns."}]

    headers = _get_sec_headers()
    cik_map = _load_ticker_cik_map(headers)

    updates = []
    results = []
    for row_idx, row in enumerate(inputs_values[1:], start=2):
        if inputs_ticker_col >= len(row):
            continue
        ticker = str(row[inputs_ticker_col]).strip().upper()
        if ticker not in active_us:
            continue
        if ticker in cache:
            data = cache[ticker]
        else:
            try:
                data = _fetch_us_fundamentals(ticker, headers, cik_map)
                cache[ticker] = data
            except Exception as exc:
                results.append({"status": "error", "ticker": ticker, "message": str(exc)})
                continue
        updates.append((row_idx, net_cash_col + 1, data.get("net_cash_bn")))
        updates.append((row_idx, fcf1_col + 1, data.get("fcf1_bn")))
        results.append({"status": "ok", "ticker": ticker, "message": "updated"})

    if updates:
        cell_list = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")
    return results


def _compute_dcf_block(row: pd.Series) -> pd.DataFrame:
    fcf1 = _safe_float(row.get("FCF1 (next-year, bn)"))
    g = _parse_rate(row.get("g (Y1-Y5)"))
    n_years = _parse_int(row.get("N (yrs)")) or 5
    g_terminal = _parse_rate(row.get("g terminal"))
    net_cash = _safe_float(row.get("Net cash/(debt) (bn)"))
    shares = _safe_float(row.get("Shares (bn)"))

    n_years = max(1, n_years)
    display_years = list(range(1, 6))

    fcf_by_year = {t: fcf1 * ((1.0 + g) ** (t - 1)) for t in range(1, n_years + 1)}

    rows = []
    for year in display_years:
        fcf_t = fcf_by_year.get(year, fcf1 * ((1.0 + g) ** (year - 1)))
        row_data = {
            "Year": year,
            "FCF": fcf_t,
        }
        for r in REQUIRED_RETURNS:
            df = 1.0 / ((1.0 + r) ** year)
            pv = fcf_t * df
            row_data[f"DF@{int(r*100)}%"] = df
            row_data[f"PV@{int(r*100)}%"] = pv
        rows.append(row_data)

    summary = {}
    for r in REQUIRED_RETURNS:
        pv_sum = 0.0
        for t in range(1, n_years + 1):
            fcf_t = fcf_by_year.get(t, fcf1 * ((1.0 + g) ** (t - 1)))
            pv_sum += fcf_t / ((1.0 + r) ** t)
        if r <= g_terminal:
            tv = None
            pv_tv = None
        else:
            fcf_n = fcf_by_year.get(n_years, fcf1 * ((1.0 + g) ** (n_years - 1)))
            tv = (fcf_n * (1.0 + g_terminal)) / (r - g_terminal)
            pv_tv = tv / ((1.0 + r) ** n_years)
        equity = None if pv_tv is None else pv_sum + pv_tv + net_cash
        fv_share = None if equity is None or shares == 0 else equity / shares
        summary[r] = {
            "tv": tv,
            "pv_tv": pv_tv,
            "equity": equity,
            "fv_share": fv_share,
        }

    tv_row = {"Year": "TV", "FCF": None}
    eq_row = {"Year": "Equity value", "FCF": None}
    fv_row = {"Year": "FV / share", "FCF": None}

    for r in REQUIRED_RETURNS:
        key = int(r * 100)
        tv_val = summary[r]["pv_tv"]
        eq_val = summary[r]["equity"]
        fv_val = summary[r]["fv_share"]
        tv_row[f"DF@{key}%"] = None
        tv_row[f"PV@{key}%"] = tv_val if tv_val is not None else None
        eq_row[f"DF@{key}%"] = None
        eq_row[f"PV@{key}%"] = eq_val if eq_val is not None else None
        fv_row[f"DF@{key}%"] = None
        fv_row[f"PV@{key}%"] = fv_val if fv_val is not None else None

    rows.extend([tv_row, eq_row, fv_row])
    df = pd.DataFrame(rows)
    if "Year" in df.columns:
        df["Year"] = df["Year"].astype(str)
    return df


def _zone_label(upside: float | None) -> str:
    if upside is None:
        return ""
    if upside >= 0.2:
        return "Green"
    if upside <= -0.2:
        return "Red"
    return "Neutral"


def _build_summary_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        ticker = str(row.get("Ticker", "")).strip().upper()
        company = str(row.get("Company", "")).strip()
        price = _safe_float(row.get("Price"))
        dcf_block = _compute_dcf_block(row)
        fv_values = {}
        for r in REQUIRED_RETURNS:
            key = f"PV@{int(r*100)}%"
            fv_row = dcf_block.loc[dcf_block["Year"] == "FV / share", key]
            fv_values[r] = float(fv_row.iloc[0]) if not fv_row.empty and fv_row.iloc[0] != "" else None
        fv10 = fv_values.get(0.10)
        upside = None if price == 0 or fv10 is None else (fv10 / price) - 1.0
        rows.append(
            {
                "Company": company,
                "Ticker": ticker,
                "Price": price,
                "FV@8%": fv_values.get(0.08),
                "FV@10%": fv_values.get(0.10),
                "FV@15%": fv_values.get(0.15),
                "FV@20%": fv_values.get(0.20),
                "Upside to FV@10%": upside,
                "Zone": _zone_label(upside),
            }
        )
    return pd.DataFrame(rows)


def _style_dcf_block(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    first_col = df.columns[0] if len(df.columns) else None
    numeric_cols = [col for col in df.columns[1:]] if len(df.columns) > 1 else []

    def _row_style(row):
        key = str(row.iloc[0]).strip().lower()
        if key in {"tv", "equity value", "fv / share", "fv/share", "fv per share"}:
            return ["font-weight: 600"] * len(row)
        return [""] * len(row)

    styler = df.style.apply(_row_style, axis=1)
    if first_col:
        styler = styler.set_properties(subset=[first_col], **{"text-align": "left"})
    if numeric_cols:
        styler = styler.set_properties(subset=numeric_cols, **{"text-align": "right"})
    return styler


@st.cache_data(ttl=600)
def _load_app_data(refresh_token: int):
    sheet_id = _get_sheet_id()
    ss, tickers_ws, inputs_ws, sources_ws = _ensure_app_tabs(sheet_id, ensure_headers=False)
    tickers_df = _read_table(tickers_ws)
    inputs_df = _map_inputs_df(_read_table(inputs_ws))
    sources_df = _map_sources_df(_read_table(sources_ws))
    tabs = [ws.title for ws in ss.worksheets()]
    return {
        "sheet_id": sheet_id,
        "sheet_title": ss.title,
        "tickers_df": tickers_df,
        "inputs_df": inputs_df,
        "sources_df": sources_df,
        "tabs": tabs,
    }


def _active_tickers(tickers_df: pd.DataFrame) -> list[str]:
    if tickers_df.empty:
        return []
    header_map = {col: _normalize(col) for col in tickers_df.columns}
    ticker_col = next((col for col, norm in header_map.items() if norm == "ticker"), None)
    active_col = next((col for col, norm in header_map.items() if norm == "active"), None)
    if not ticker_col:
        return []
    active_values = set()
    if active_col:
        for _, row in tickers_df.iterrows():
            ticker = str(row.get(ticker_col, "")).strip().upper()
            active = str(row.get(active_col, "")).strip().upper()
            if ticker and active in {"TRUE", "YES", "1"}:
                active_values.add(ticker)
    else:
        active_values = {str(t).strip().upper() for t in tickers_df[ticker_col] if str(t).strip()}
    return sorted(active_values)


def _ticker_markets(tickers_df: pd.DataFrame) -> dict[str, str]:
    if tickers_df.empty:
        return {}
    header_map = {col: _normalize(col) for col in tickers_df.columns}
    ticker_col = next((col for col, norm in header_map.items() if norm == "ticker"), None)
    market_col = next((col for col, norm in header_map.items() if norm == "market"), None)
    market_map = {}
    if not ticker_col or not market_col:
        return market_map
    for _, row in tickers_df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip().upper()
        market = str(row.get(market_col, "")).strip().upper()
        if ticker and market:
            market_map[ticker] = market
    return market_map


def _normalize_ticker(value: str) -> str:
    return str(value or "").strip().upper()


def _ticker_key(value: str) -> str:
    text = _normalize_ticker(value)
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    return text


def _refresh():
    st.session_state["template_refresh_token"] = st.session_state.get("template_refresh_token", 0) + 1
    st.rerun()


def _df_has_ticker(df: pd.DataFrame, ticker: str) -> bool:
    if df.empty:
        return False
    if "Ticker" not in df.columns:
        return False
    return any(_ticker_key(value) == _ticker_key(ticker) for value in df["Ticker"].fillna("").tolist())


try:
    data = _load_app_data(refresh_token)
except Exception as exc:
    st.error(f"Template load failed: {exc}")
    st.stop()

inputs_df = data["inputs_df"]
sources_df = data["sources_df"]
tickers_df = data["tickers_df"]

sheet_id = data["sheet_id"]
ss, tickers_ws, inputs_ws, sources_ws = _ensure_app_tabs(sheet_id, ensure_headers=False)

active_tickers = _active_tickers(tickers_df)
market_map = _ticker_markets(tickers_df)
has_ticker_table = not tickers_df.empty

with st.sidebar:
    st.subheader("Add ticker")
    with st.form("add_ticker_form"):
        ticker_value = st.text_input("Ticker")
        market_value = st.selectbox("Market", options=["US", "NZ"])
        add_submitted = st.form_submit_button("Add ticker")
        if add_submitted:
            ticker = _normalize_ticker(ticker_value)
            if not ticker:
                st.warning("Ticker is required.")
            else:
                try:
                    if not st.session_state.get("headers_ensured"):
                        _ensure_headers(tickers_ws, APP_TICKERS_HEADERS)
                        _ensure_headers(inputs_ws, APP_INPUTS_HEADERS)
                        _ensure_headers(sources_ws, APP_SOURCES_HEADERS)
                        st.session_state["headers_ensured"] = True
                    _seed_app_inputs_formulas(inputs_ws)
                    if _df_has_ticker(tickers_df, ticker):
                        _set_ticker_active(tickers_ws, ticker, True)
                    else:
                        _append_ticker_row(tickers_ws, ticker, market_value)
                    _dedupe_tickers_ws(tickers_ws)
                    if not _df_has_ticker(inputs_df, ticker):
                        _append_inputs_row(inputs_ws, ticker, market_value)
                    if not _df_has_ticker(sources_df, ticker):
                        _seed_sources(sources_ws, ticker, market_value)
                    _dedupe_inputs_ws(inputs_ws)
                    _dedupe_sources_ws(sources_ws)
                    _refresh()
                except Exception as exc:
                    st.error(f"Add ticker failed: {exc}")

    st.subheader("Remove ticker")
    with st.form("remove_ticker_form"):
        remove_ticker = st.selectbox("Ticker", options=[""] + active_tickers)
        remove_submitted = st.form_submit_button("Remove ticker")
        if remove_submitted:
            if not remove_ticker:
                st.warning("Select a ticker to remove.")
            else:
                if not st.session_state.get("headers_ensured"):
                    _ensure_headers(tickers_ws, APP_TICKERS_HEADERS)
                    _ensure_headers(inputs_ws, APP_INPUTS_HEADERS)
                    _ensure_headers(sources_ws, APP_SOURCES_HEADERS)
                    st.session_state["headers_ensured"] = True
                _set_ticker_active(tickers_ws, remove_ticker, False)
                _dedupe_tickers_ws(tickers_ws)
                _dedupe_inputs_ws(inputs_ws)
                _dedupe_sources_ws(sources_ws)
                _load_app_data.clear()
                _refresh()
    if st.button("Fix/Seed formulas"):
        try:
            _ensure_headers(inputs_ws, APP_INPUTS_HEADERS)
            _seed_app_inputs_formulas(inputs_ws)
            st.success("APP_INPUTS formulas seeded in row 2.")
        except Exception as exc:
            st.error(f"Formula seeding failed: {exc}")
    if st.button("Autofill US fundamentals"):
        try:
            cache = st.session_state.setdefault("us_fund_cache", {})
            with st.status("Pulling US fundamentals...", expanded=True) as status:
                results = _autofill_us_fundamentals(tickers_ws, inputs_ws, cache)
                for result in results:
                    if result["status"] == "ok":
                        st.write(f"{result['ticker']}: updated")
                    else:
                        st.write(f"{result['ticker']}: {result['message']}")
                status.update(label="US fundamentals refresh complete", state="complete")
            _load_app_data.clear()
            _refresh()
        except Exception as exc:
            st.error(f"US fundamentals failed: {exc}")

with st.expander("Template Debug", expanded=False):
    st.write("Sheet title:", data["sheet_title"])
    st.write("Sheet ID:", sheet_id[:6] + "..." + sheet_id[-6:])
    st.write("Tabs:", data["tabs"])

st.subheader("Inputs")
if inputs_df.empty:
    st.info("APP_INPUTS is empty.")
else:
    if has_ticker_table:
        inputs_view = inputs_df[inputs_df["Ticker"].str.upper().isin(active_tickers)].copy()
    else:
        inputs_view = inputs_df
    st.dataframe(inputs_view, use_container_width=True)

st.subheader("Fair Value Table")
if inputs_df.empty:
    st.info("APP_INPUTS is empty.")
else:
    if has_ticker_table:
        summary_df = _build_summary_table(inputs_df[inputs_df["Ticker"].str.upper().isin(active_tickers)].copy())
    else:
        summary_df = _build_summary_table(inputs_df)
    st.dataframe(summary_df, use_container_width=True)

st.subheader("DCF Blocks (5-year)")
if inputs_df.empty:
    st.info("APP_INPUTS is empty.")
else:
    if has_ticker_table:
        blocks_df = inputs_df[inputs_df["Ticker"].str.upper().isin(active_tickers)].copy()
    else:
        blocks_df = inputs_df
    for _, row in blocks_df.iterrows():
        company = str(row.get("Company", "")).strip()
        ticker = str(row.get("Ticker", "")).strip()
        if not ticker:
            continue
        title = f"{company} ({ticker})" if company else ticker
        with st.expander(title, expanded=False):
            block_df = _compute_dcf_block(row)
            st.dataframe(_style_dcf_block(block_df), use_container_width=True)

with st.expander("Sources", expanded=False):
    if sources_df.empty:
        st.info("APP_SOURCES is empty.")
    else:
        if has_ticker_table:
            filtered = sources_df[sources_df["Ticker"].str.upper().isin(active_tickers)].copy()
        else:
            filtered = sources_df
        filter_options = ["All"] + sorted({str(t).strip().upper() for t in filtered.get("Ticker", []) if str(t).strip()})
        selected = st.selectbox("Filter by ticker", options=filter_options)
        if selected != "All":
            filtered = filtered[filtered["Ticker"].str.upper() == selected]
        st.dataframe(filtered, use_container_width=True)
