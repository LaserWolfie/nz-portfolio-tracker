from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import List

import pandas as pd
import streamlit as st

from src.data.sheets import get_gspread_client
from src.services.return_ladder_app_inputs import (
    APP_INPUTS_HEADERS as APP_INPUTS_SCHEMA_HEADERS,
    ensure_app_inputs_schema,
    seed_app_inputs_formulas,
)
from src.services.nz_sources_lookup import load_sources_wide
from src.services.sec_fundamentals import (
    SEC_USER_AGENT_HELP,
    SEC_FACTS_URL,
    fetch_us_fundamentals,
    get_net_cash_debt_bn,
    get_sec_headers,
    get_sec_user_agent,
)
from src.services.nzx_instruments import get_nzx_snapshot
from src.services.sources_registry import find_best_source, load_sources


st.set_page_config(page_title="Return Ladder (Template Viewer)", page_icon="\U0001F4C4", layout="wide")

st.title("Return Ladder (Template Viewer)")
with st.expander("About this page (Toy Model)", expanded=False):
    st.markdown(
        "📉 Return Ladder (Template Viewer)  Toy Model (Quick Valuation)\n"
        "This page is a toy valuation model designed for fast screening and sensitivity analysis. "
        "It helps you sanity-check a stock by showing how the implied fair value per share changes as your "
        "required return (discount rate) changes.\n\n"
        "Important: This is intentionally simplified and directional  it is not a full, audit-grade DCF. "
        "Keep units consistent: Shares_bn, Net cash/(debt) (bn), and FCF1 (bn) must all be in billions and "
        "in the same currency. If the company has net debt, enter it as a negative number.\n\n"
        "Roadmap: A full DCF model will be added in a later release (more complete cash-flow build including "
        "operating drivers, taxes, capex, working capital, and dilution)."
    )
with st.expander("Toy model checklist (whats left)", expanded=False):
    st.markdown(
        "- [ ] Fix SAN: keep APP_SOURCES Field=\"Company\" pointing to NZX instrument URL; store annual report under Field=\"Annual report (PDF)\"\n"
        "- [ ] NZ NetCash/FCF1: Gemini extracts values  write into Sources tab columns NetCash_bn and FCF1_bn\n"
        "- [ ] NZ Autofill button should read Sources  fill APP_INPUTS Net cash/(debt) (bn) and FCF1 only if blank/seeded-zero, and append URL into Links\n"
        "- [ ] Alias matching (EBO  EBOS)\n"
        "- [ ] No cell notes; Links/SOURCES_LOG only"
    )
st.caption("Template viewer with app-side DCF calculations and write-back to app tabs.")

logger = logging.getLogger(__name__)

APP_TICKERS_TAB = "APP_TICKERS"
APP_INPUTS_TAB = "APP_INPUTS"
APP_SOURCES_TAB = "APP_SOURCES"

APP_TICKERS_HEADERS = ["Ticker", "Market", "Active", "AddedAt"]
APP_INPUTS_HEADERS = APP_INPUTS_SCHEMA_HEADERS
APP_SOURCES_HEADERS = ["Ticker", "Metric", "URL", "Notes", "UpdatedAt"]
SOURCES_LOG_TAB = "SOURCES_LOG"
SOURCES_LOG_HEADERS = ["Timestamp", "Ticker", "Field", "Value", "SourceURL"]

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
        ensure_app_inputs_schema(inputs_ws)
    if ensure_headers:
        ensure_app_inputs_schema(inputs_ws)

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
        "sharesbn": "Shares_bn",
        "netcashdebtbn": "Net cash/(debt) (bn)",
        "netcashbn": "Net cash/(debt) (bn)",
        "fcf1nextyearbn": "FCF1 (next-year, bn)",
        "fcf1bn": "FCF1 (next-year, bn)",
        "gy1y5": "g (Y1-Y5)",
        "g15": "g (Y1-Y5)",
        "nyrs": "N (yrs)",
        "nyears": "N (yrs)",
        "n": "N (yrs)",
        "gterminal": "g_terminal",
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


def _is_blank_or_zero(value) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    parsed = _parse_float(text)
    return parsed is None or parsed == 0.0


def _is_blank_or_zero_strict(value) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    parsed = _parse_float(text)
    if parsed is None:
        return False
    return parsed == 0.0


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


def _append_sources_rows(sources_ws, rows: list[list[str]]):
    if rows:
        sources_ws.append_rows(rows, value_input_option="USER_ENTERED")


def _ensure_sources_log(ss):
    for ws in ss.worksheets():
        if ws.title == SOURCES_LOG_TAB:
            return ws
    ws = ss.add_worksheet(title=SOURCES_LOG_TAB, rows=500, cols=len(SOURCES_LOG_HEADERS) + 2)
    _ensure_headers(ws, SOURCES_LOG_HEADERS, assume_empty=True)
    return ws


def _append_sources_log(ss, rows: list[list[str]]):
    if not rows:
        return
    log_ws = _ensure_sources_log(ss)
    log_ws.append_rows(rows, value_input_option="USER_ENTERED")


def _append_inputs_row(inputs_ws, ticker: str, market: str) -> int | None:
    values = inputs_ws.get_all_values()
    if not values or len(values) < 1:
        raise RuntimeError("APP_INPUTS must have a header row.")
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    last_row = _last_non_empty_row(values, ticker_col)
    target_row = last_row + 1

    if target_row > inputs_ws.row_count:
        inputs_ws.add_rows(target_row - inputs_ws.row_count)

    updates = []
    if ticker_col is not None:
        updates.append((target_row, ticker_col + 1, ticker))
    market_col = _find_col_index(header, "Market")
    if market_col is not None:
        updates.append((target_row, market_col + 1, market))
    if updates:
        cell_list = [inputs_ws.cell(row, col) for row, col, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")
    return target_row


def _set_inputs_market_value(inputs_ws, row_idx: int, market: str):
    header = inputs_ws.row_values(1)
    market_col = _find_col_index(header, "Market")
    if market_col is None:
        return
    cell = inputs_ws.cell(row_idx, market_col + 1)
    cell.value = market
    inputs_ws.update_cells([cell], value_input_option="USER_ENTERED")


def _force_market_for_ticker(inputs_ws, ticker: str, market: str):
    values = inputs_ws.get_all_values()
    if not values:
        return
    header = values[0]
    ticker_col = _find_col_index(header, "Ticker")
    market_col = _find_col_index(header, "Market")
    if ticker_col is None or market_col is None:
        return
    updates = []
    for row_idx, row in enumerate(values[1:], start=2):
        if ticker_col >= len(row):
            continue
        row_ticker = str(row[ticker_col]).strip().upper()
        if row_ticker == str(ticker).strip().upper():
            updates.append((row_idx, market_col + 1, market))
    if updates:
        cells = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cells, updates):
            cell.value = value
        inputs_ws.update_cells(cells, value_input_option="USER_ENTERED")


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




def _autofill_us_fundamentals(tickers_ws, inputs_ws, sources_ws, cache: dict, user_agent: str) -> list[dict]:
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
    company_col = _find_col_index(inputs_header, "Company")
    shares_col = _find_col_index(inputs_header, "Shares_bn")
    net_cash_col = _find_col_index(inputs_header, "Net cash/(debt) (bn)")
    fcf1_col = _find_col_index(inputs_header, "FCF1 (next-year, bn)")
    g_col = _find_col_index(inputs_header, "g (Y1-Y5)")
    if inputs_ticker_col is None or fcf1_col is None:
        return [{"status": "error", "ticker": "", "message": "APP_INPUTS missing required columns."}]

    headers = get_sec_headers(user_agent)

    updates = []
    source_rows = []
    log_rows = []
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
                data = fetch_us_fundamentals(ticker, headers)
                cache[ticker] = data
            except Exception as exc:
                results.append({"status": "error", "ticker": ticker, "message": str(exc)})
                logger.exception("US fundamentals fetch failed for %s", ticker)
                continue

        row_company = str(row[company_col]).strip() if company_col is not None and company_col < len(row) else ""
        row_shares = str(row[shares_col]).strip() if shares_col is not None and shares_col < len(row) else ""
        row_net_cash = row[net_cash_col] if net_cash_col is not None and net_cash_col < len(row) else ""
        row_fcf1 = row[fcf1_col] if fcf1_col is not None and fcf1_col < len(row) else ""
        row_g = str(row[g_col]).strip() if g_col is not None and g_col < len(row) else ""

        if company_col is not None and not row_company and data.get("company_name"):
            updates.append((row_idx, company_col + 1, data.get("company_name")))
        if shares_col is not None and not row_shares and data.get("shares_bn") is not None:
            updates.append((row_idx, shares_col + 1, data.get("shares_bn")))
            source_rows.append(
                [
                    ticker,
                    "Shares_bn",
                    SEC_FACTS_URL.format(cik=data.get("cik", "")),
                    "auto-seeded",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ]
            )
        net_cash_updated = False
        net_cash_missing = False
        if net_cash_col is not None and _is_blank_or_zero(row_net_cash):
            net_cash_bn = data.get("net_cash_bn")
            if net_cash_bn is None:
                try:
                    net_cash_bn = get_net_cash_debt_bn(ticker, headers)
                except Exception as exc:
                    results.append({"status": "error", "ticker": ticker, "message": str(exc)})
                    logger.exception("Net cash fetch failed for %s", ticker)
                    continue
            if net_cash_bn is not None:
                updates.append((row_idx, net_cash_col + 1, net_cash_bn))
                net_cash_updated = True
                source_rows.append(
                    [
                        ticker,
                        "Net cash/(debt) (bn)",
                        SEC_FACTS_URL.format(cik=data.get("cik", "")),
                        "auto-seeded",
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ]
                )
                log_rows.append(
                    [
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        ticker,
                        "Net cash/(debt) (bn)",
                        str(net_cash_bn),
                        SEC_FACTS_URL.format(cik=data.get("cik", "")),
                    ]
                )
            else:
                net_cash_missing = True
        fcf1_updated = False
        fcf1_error = None
        if fcf1_col is not None and _is_blank_or_zero(row_fcf1):
            fcf_bn = data.get("fcf_bn")
            if fcf_bn is not None:
                g_rate = _parse_rate(row_g) if row_g else 0.03
                fcf1_bn = fcf_bn * (1.0 + g_rate)
                updates.append((row_idx, fcf1_col + 1, fcf1_bn))
                fcf1_updated = True
                source_rows.append(
                    [
                        ticker,
                        "FCF1 (next-year, bn)",
                        SEC_FACTS_URL.format(cik=data.get("cik", "")),
                        "auto-seeded",
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ]
                )
            else:
                fcf1_error = "FCF unavailable from SEC"

        results.append(
            {
                "status": "ok",
                "ticker": ticker,
                "message": "updated",
                "net_cash_updated": net_cash_updated,
                "net_cash_missing": net_cash_missing,
                "fcf1_updated": fcf1_updated,
                "fcf1_error": fcf1_error,
            }
        )

    if updates:
        cell_list = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")
    _append_sources_rows(sources_ws, source_rows)
    _append_sources_log(inputs_ws.spreadsheet, log_rows)
    return results


def _autofill_nz_fundamentals(inputs_ws, sources_ws, ss, sources_tab: str) -> dict:
    inputs_values = inputs_ws.get_all_values()
    if not inputs_values:
        return {"results": [{"status": "error", "ticker": "", "message": "APP_INPUTS is empty."}], "debug": [], "warnings": []}
    inputs_header = inputs_values[0]
    ticker_col = _find_col_index(inputs_header, "Ticker")
    market_col = _find_col_index(inputs_header, "Market")
    shares_col = _find_col_index(inputs_header, "Shares_bn")
    company_col = _find_col_index(inputs_header, "Company")
    net_cash_col = _find_col_index(inputs_header, "Net cash/(debt) (bn)")
    fcf1_col = _find_col_index(inputs_header, "FCF1 (next-year, bn)")
    links_col = _find_col_index(inputs_header, "Links")
    if ticker_col is None or market_col is None or shares_col is None or company_col is None:
        return {
            "results": [{"status": "error", "ticker": "", "message": "APP_INPUTS missing required columns."}],
            "debug": [],
            "warnings": [],
        }

    updates = []
    source_rows = []
    log_rows = []
    results = []
    debug_rows = []
    warnings = []
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sources = load_sources(ss, sources_tab)
    for row_idx, row in enumerate(inputs_values[1:], start=2):
        if ticker_col >= len(row):
            continue
        ticker = str(row[ticker_col]).strip().upper()
        if not ticker:
            continue
        market = str(row[market_col]).strip().upper() if market_col < len(row) else ""
        if market != "NZ":
            continue
        ticker_norm = _normalize_nz_ticker(ticker)
        shares_value = row[shares_col] if shares_col < len(row) else ""
        company_value = row[company_col] if company_col < len(row) else ""
        net_cash_value = row[net_cash_col] if net_cash_col is not None and net_cash_col < len(row) else ""
        fcf1_value = row[fcf1_col] if fcf1_col is not None and fcf1_col < len(row) else ""
        code = ticker.replace("NZX:", "").replace("NZ:", "")
        company_text = str(company_value or "").strip()
        company_missing = (
            not company_text
            or company_text.upper() == ticker.upper()
            or company_text.upper() == code.upper()
        )
        source_entry = find_best_source(sources, ticker_norm, company_text or None)
        source_url = source_entry.get("url") if source_entry else None
        debug_rows.append(
            {
                "ticker_raw": ticker,
                "ticker_norm": ticker_norm,
                "source_key": source_entry.get("key") if source_entry else None,
                "source_url": source_url,
                "netcash_bn": source_entry.get("netcash_bn") if source_entry else None,
                "fcf1_bn": source_entry.get("fcf1_bn") if source_entry else None,
            }
        )
        if not _is_blank_or_zero(shares_value) and not company_missing and not _is_blank_or_zero(net_cash_value) and not _is_blank_or_zero(fcf1_value):
            results.append({"status": "ok", "ticker": ticker, "message": "skipped"})
            continue
        try:
            snapshot = get_nzx_snapshot(ticker)
        except Exception as exc:
            results.append({"status": "error", "ticker": ticker, "message": str(exc)})
            continue
        url = snapshot.get("source_url")
        shares_bn = snapshot.get("shares_bn")
        company_name = snapshot.get("company")
        if shares_bn is not None and _is_blank_or_zero(shares_value):
            updates.append((row_idx, shares_col + 1, shares_bn))
            source_rows.append([ticker, "Shares_bn", url, "auto-seeded", timestamp])
            log_rows.append([timestamp, ticker, "Shares_bn", str(shares_bn), url])
        if company_name and company_missing:
            updates.append((row_idx, company_col + 1, company_name))
            source_rows.append([ticker, "Company", url, "auto-seeded", timestamp])
            log_rows.append([timestamp, ticker, "Company", company_name, url])
        if net_cash_col is not None and _is_blank_or_zero(net_cash_value):
            netcash_bn = source_entry.get("netcash_bn") if source_entry else None
            if netcash_bn is not None:
                updates.append((row_idx, net_cash_col + 1, netcash_bn))
            else:
                warn = f"Missing NetCash_bn in Sources for {ticker_norm}"
                warnings.append(warn)
                print(warn)
        if fcf1_col is not None and _is_blank_or_zero(fcf1_value):
            fcf1_bn = source_entry.get("fcf1_bn") if source_entry else None
            if fcf1_bn is not None:
                updates.append((row_idx, fcf1_col + 1, fcf1_bn))
            else:
                warn = f"Missing FCF1_bn in Sources for {ticker_norm}"
                warnings.append(warn)
                print(warn)
        link_to_write = source_url or url
        if links_col is not None and link_to_write and (links_col >= len(row) or str(row[links_col]).strip() == ""):
            updates.append((row_idx, links_col + 1, link_to_write))
        if shares_bn is None and not company_name:
            results.append({"status": "error", "ticker": ticker, "message": "Snapshot missing data"})
            continue
        if company_name is None:
            results.append({"status": "ok", "ticker": ticker, "message": "missing company"})
        else:
            results.append({"status": "ok", "ticker": ticker, "message": "updated"})

    if updates:
        cell_list = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cell_list, updates):
            cell.value = value
        inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")
    _append_sources_rows(sources_ws, source_rows)
    _append_sources_log(inputs_ws.spreadsheet, log_rows)
    return {"results": results, "debug": debug_rows, "warnings": warnings}


def _compute_dcf_block(row: pd.Series) -> pd.DataFrame:
    fcf1 = _safe_float(row.get("FCF1 (next-year, bn)"))
    g = _parse_rate(row.get("g (Y1-Y5)"))
    n_years = _parse_int(row.get("N (yrs)")) or 5
    g_terminal = _parse_rate(row.get("g_terminal"))
    net_cash = _safe_float(row.get("Net cash/(debt) (bn)"))
    shares = _safe_float(row.get("Shares_bn"))

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


def _normalize_nz_ticker(value: str) -> str:
    text = _normalize_ticker(value)
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
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
                        ensure_app_inputs_schema(inputs_ws)
                        _ensure_headers(sources_ws, APP_SOURCES_HEADERS)
                        st.session_state["headers_ensured"] = True
                    if _df_has_ticker(tickers_df, ticker):
                        _set_ticker_active(tickers_ws, ticker, True)
                    else:
                        _append_ticker_row(tickers_ws, ticker, market_value)
                    _dedupe_tickers_ws(tickers_ws)
                    if not _df_has_ticker(inputs_df, ticker):
                        row_idx = _append_inputs_row(inputs_ws, ticker, market_value)
                        seed_app_inputs_formulas(inputs_ws, {ticker.upper(): market_value})
                        if row_idx:
                            _set_inputs_market_value(inputs_ws, row_idx, market_value)
                        _force_market_for_ticker(inputs_ws, ticker, market_value)
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
                    ensure_app_inputs_schema(inputs_ws)
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
            ensure_app_inputs_schema(inputs_ws)
            seed_app_inputs_formulas(inputs_ws)
            st.success("APP_INPUTS formulas seeded.")
        except Exception as exc:
            st.error(f"Formula seeding failed: {exc}")
    if st.button("Autofill US fundamentals"):
        user_agent = get_sec_user_agent()
        if not user_agent:
            st.error(SEC_USER_AGENT_HELP)
        else:
            try:
                cache = st.session_state.setdefault("us_fund_cache", {})
                with st.status("Pulling US fundamentals...", expanded=True) as status:
                    results = _autofill_us_fundamentals(tickers_ws, inputs_ws, sources_ws, cache, user_agent)
                    fcf1_filled = []
                    fcf1_failed = []
                    net_cash_filled = []
                    net_cash_missing = []
                    errors = []
                    for result in results:
                        if result["status"] == "ok":
                            st.write(f"{result['ticker']}: updated")
                        else:
                            st.write(f"{result['ticker']}: {result['message']}")
                            errors.append(f"{result['ticker']}: {result['message']}")
                        if result.get("fcf1_updated"):
                            fcf1_filled.append(result["ticker"])
                        elif result.get("fcf1_error"):
                            fcf1_failed.append(f"{result['ticker']} ({result['fcf1_error']})")
                        if result.get("net_cash_updated"):
                            net_cash_filled.append(result["ticker"])
                        elif result.get("net_cash_missing"):
                            net_cash_missing.append(result["ticker"])
                    if fcf1_filled:
                        st.write("FCF1 filled for:", ", ".join(fcf1_filled))
                    if fcf1_failed:
                        st.write("FCF1 failed for:", ", ".join(fcf1_failed))
                    if net_cash_filled:
                        st.write("Net cash/(debt) filled for:", ", ".join(net_cash_filled))
                    if net_cash_missing:
                        st.warning("Missing net cash/(debt) for: " + ", ".join(net_cash_missing))
                    for entry in errors:
                        st.error(entry)
                    status.update(label="US fundamentals refresh complete", state="complete")
                _load_app_data.clear()
                _refresh()
            except Exception as exc:
                st.error(f"US fundamentals failed: {exc}")
    if st.button("Autofill NZ fundamentals"):
        try:
            with st.status("Pulling NZ fundamentals...", expanded=True) as status:
                sources_map = load_sources_wide(sheet_id)
                inputs_values = inputs_ws.get_all_values()
                if not inputs_values:
                    st.error("APP_INPUTS is empty.")
                    status.update(label="NZ fundamentals refresh complete", state="complete")
                    st.stop()

                header = inputs_values[0]
                ticker_col = _find_col_index(header, "Ticker")
                market_col = _find_col_index(header, "Market")
                net_cash_col = _find_col_index(header, "Net cash/(debt) (bn)")
                fcf1_col = _find_col_index(header, "FCF1 (next-year, bn)")
                links_col = _find_col_index(header, "Links")
                notes_col = _find_col_index(header, "Notes")

                if None in (ticker_col, market_col, net_cash_col, fcf1_col, links_col, notes_col):
                    st.error("APP_INPUTS missing required columns.")
                    status.update(label="NZ fundamentals refresh complete", state="complete")
                    st.stop()

                updates = []
                updated_tickers = set()
                missing_sources = set()
                fields_updated = {"net_cash": 0, "fcf1": 0, "links": 0}

                for row_idx, row in enumerate(inputs_values[1:], start=2):
                    ticker = str(row[ticker_col]).strip() if ticker_col < len(row) else ""
                    if not ticker:
                        continue
                    market = str(row[market_col]).strip().upper() if market_col < len(row) else ""
                    if market != "NZ":
                        continue
                    notes = str(row[notes_col]).strip().lower() if notes_col < len(row) else ""
                    notes_ok = not notes or "auto" in notes
                    if not notes_ok:
                        continue

                    net_cash_value = row[net_cash_col] if net_cash_col < len(row) else ""
                    fcf1_value = row[fcf1_col] if fcf1_col < len(row) else ""
                    net_cash_blank = _is_blank_or_zero_strict(net_cash_value)
                    fcf1_blank = _is_blank_or_zero_strict(fcf1_value)
                    if not net_cash_blank and not fcf1_blank:
                        continue

                    ticker_norm = _normalize_nz_ticker(ticker)
                    source_entry = sources_map.get(ticker_norm)
                    if not source_entry:
                        print(f"NZ Sources missing for {ticker_norm}")
                        missing_sources.add(ticker_norm)
                        continue

                    row_updated = False
                    if net_cash_blank and source_entry.get("netcash_bn") is not None:
                        updates.append((row_idx, net_cash_col + 1, source_entry.get("netcash_bn")))
                        fields_updated["net_cash"] += 1
                        row_updated = True
                    elif net_cash_blank:
                        print(f"NZ Sources missing NetCash_bn for {ticker_norm}")

                    if fcf1_blank and source_entry.get("fcf1_bn") is not None:
                        updates.append((row_idx, fcf1_col + 1, source_entry.get("fcf1_bn")))
                        fields_updated["fcf1"] += 1
                        row_updated = True
                    elif fcf1_blank:
                        print(f"NZ Sources missing FCF1_bn for {ticker_norm}")

                    if row_updated:
                        updated_tickers.add(ticker_norm)
                        url = str(source_entry.get("url") or "").strip()
                        if url and links_col < len(row):
                            existing_links = str(row[links_col] or "").strip()
                            if url not in existing_links:
                                new_links = f"{existing_links}\n{url}" if existing_links else url
                                updates.append((row_idx, links_col + 1, new_links))
                                fields_updated["links"] += 1

                if updates:
                    cell_list = [inputs_ws.cell(r, c) for r, c, _ in updates]
                    for cell, (_, _, value) in zip(cell_list, updates):
                        cell.value = value
                    inputs_ws.update_cells(cell_list, value_input_option="USER_ENTERED")

                if updated_tickers:
                    st.write("Tickers updated:", ", ".join(sorted(updated_tickers)))
                st.write(
                    "Fields updated:",
                    f"net_cash={fields_updated.get('net_cash', 0)}, "
                    f"fcf1={fields_updated.get('fcf1', 0)}, "
                    f"links={fields_updated.get('links', 0)}",
                )
                if missing_sources:
                    st.warning("Missing Sources data for: " + ", ".join(sorted(missing_sources)))
                status.update(label="NZ fundamentals refresh complete", state="complete")
            _load_app_data.clear()
            _refresh()
        except Exception as exc:
            st.error(f"NZ fundamentals failed: {exc}")

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
