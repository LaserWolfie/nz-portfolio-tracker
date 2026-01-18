import math
import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from gspread.utils import rowcol_to_a1

from src.data.sheets import get_gspread_client
from src.services.fundamentals import (
    compute_cashflow_from_companyfacts_detailed,
    compute_net_cash_from_companyfacts_detailed,
    fetch_sec_companyfacts,
    fetch_sec_company_tickers,
    get_cik_for_ticker,
)
from src.services.market_data import fetch_nz_quote, fetch_us_quote
from src.services.return_ladder_dcf import (
    DCFInputs,
    build_dcf,
    build_summary_row,
    coerce_inputs_df,
    validate_rows,
)


st.set_page_config(page_title="NZ Wealth Manager Pro - Return Ladder", page_icon="🪜", layout="wide")
st.title("🪜 NZ Wealth Manager Pro - Return Ladder")
st.caption("DCF-style return ladders with live market data and per-ticker cashflow tables.")

DEFAULT_REQUIRED_RETURNS = [0.08, 0.10, 0.15, 0.20]
MARKET_CACHE_TAB = "MARKET_CACHE"
INPUTS_MASTER_TAB = "INPUTS_MASTER"
MARKET_CACHE_COLUMNS = [
    "Symbol",
    "Market",
    "CCY",
    "Price",
    "Shares_bn",
    "NetCash_bn",
    "CFO_bn",
    "Capex_bn",
    "FCF_bn",
    "FCF1_bn",
    "G_5Y",
    "G_Terminal",
    "Years",
    "Exit_Multiple",
    "UpdatedAt_UTC",
    "Source",
]

REQUIRED_MARKET_CACHE_COLUMNS = [
    "Symbol",
    "Market",
    "CCY",
    "Price",
    "Shares_bn",
    "NetCash_bn",
    "FCF1_bn",
    "G_5Y",
    "Years",
    "G_Terminal",
    "UpdatedAt_UTC",
    "Source",
]

SECRET_KEY_CANDIDATES = [
    "return_ladder_sheet_id",
    "RETURN_LADDER_SHEET_ID",
    "return_ladder_sheet",
    "RETURN_LADDER_SHEET",
]

MARKET_CACHE_SYNONYMS = {
    "symbol": "Symbol",
    "ticker": "Symbol",
    "code": "Symbol",
    "market": "Market",
    "ccy": "CCY",
    "currency": "CCY",
    "price": "Price",
    "currentprice": "Price",
    "price_nzd": "Price",
    "sharesbn": "Shares_bn",
    "shares_out": "Shares_bn",
    "shares": "Shares_bn",
    "netcashbn": "NetCash_bn",
    "netcash": "NetCash_bn",
    "netdebt": "NetCash_bn",
    "cfo": "CFO_bn",
    "capex": "Capex_bn",
    "fcf": "FCF_bn",
    "fcf1bn": "FCF1_bn",
    "fcf1": "FCF1_bn",
    "g_5y": "G_5Y",
    "g5y": "G_5Y",
    "gterminal": "G_Terminal",
    "g_terminal": "G_Terminal",
    "years": "Years",
    "yearstoexit": "Years",
    "exit_multiple": "Exit_Multiple",
    "exitmultiple": "Exit_Multiple",
    "updatedatutc": "UpdatedAt_UTC",
    "updatedat": "UpdatedAt_UTC",
    "updated": "UpdatedAt_UTC",
    "source": "Source",
}

INPUTS_MASTER_SYNONYMS = {
    "symbol": "Symbol",
    "ticker": "Symbol",
    "code": "Symbol",
    "market": "Market",
    "ccy": "CCY",
    "currency": "CCY",
    "netcashbn": "NetCash_bn",
    "netcash": "NetCash_bn",
    "netdebt": "NetCash_bn",
    "fcfbn": "FCF_bn",
    "fcf": "FCF_bn",
    "fcf1bn": "FCF1_bn",
    "fcf1": "FCF1_bn",
    "g_5y": "G_5Y",
    "g5y": "G_5Y",
    "gterminal": "G_Terminal",
    "g_terminal": "G_Terminal",
    "years": "Years",
    "yearstoexit": "Years",
    "exit_multiple": "Exit_Multiple",
    "exitmultiple": "Exit_Multiple",
    "sharesbn": "Shares_bn",
    "shares_out": "Shares_bn",
    "shares": "Shares_bn",
}


def _parse_required_returns(raw: str) -> list[float]:
    returns = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = float(part)
        except ValueError:
            continue
        if value > 1.0:
            value = value / 100.0
        returns.append(value)
    returns = sorted(set(returns))
    return returns or DEFAULT_REQUIRED_RETURNS


def _parse_price(value: str) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _parse_rate(value) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if text == "":
        return 0.0
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return 0.0
    return number / 100.0 if is_percent else number


def _should_overwrite(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _needs_fundamentals(row: dict) -> bool:
    return any(
        _should_overwrite(row.get(field))
        for field in ("net_cash_or_debt", "cfo_bn", "capex_bn", "fcf_bn", "fcf_year0")
    )


def _is_blank_or_zero(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        if value.strip() == "":
            return True
        try:
            return float(value) == 0.0
        except ValueError:
            return False
    try:
        if pd.isna(value):
            return True
        return float(value) == 0.0
    except Exception:
        return False


def _blank_if_zero(value):
    if _is_blank_or_zero(value):
        return ""
    return value


def _resolve_fcf1(row: dict, ticker: str, warnings: list[str]) -> float | None:
    fcf1 = row.get("fcf1")
    if _is_blank_or_zero(fcf1):
        fcf1_bn = row.get("fcf1_bn")
        if not _is_blank_or_zero(fcf1_bn):
            try:
                fcf1 = float(fcf1_bn) * 1e9
            except (TypeError, ValueError):
                fcf1 = None
    if _is_blank_or_zero(fcf1):
        fcf_year0 = row.get("fcf_year0")
        if not _is_blank_or_zero(fcf_year0):
            warnings.append(f"{ticker}: FCF1 missing; using trailing FCF as FCF1.")
            fcf1 = fcf_year0
    return None if _is_blank_or_zero(fcf1) else float(fcf1)


def _is_invalid_number(value) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not math.isfinite(float(value))
    return False


def _sanitize_sheet_value(value):
    if _is_invalid_number(value):
        return None
    return value


def _sanitize_sheet_row(row_values: list):
    return [_sanitize_sheet_value(value) for value in row_values]


def _get_secret(keys: list[str]) -> str | None:
    for key in keys:
        value = st.secrets.get(key)
        if value:
            return str(value).strip()
    google_sheets = st.secrets.get("google_sheets") or {}
    if isinstance(google_sheets, dict):
        for key in keys:
            value = google_sheets.get(key)
            if value:
                return str(value).strip()
    for key in keys:
        value = os.getenv(key.upper())
        if value:
            return str(value).strip()
    override = st.session_state.get("return_ladder_sheet_id_override")
    if override:
        return str(override).strip()
    return None


def _mask_sheet_id(sheet_id: str) -> str:
    if not sheet_id:
        return ""
    if len(sheet_id) <= 10:
        return sheet_id
    return f"{sheet_id[:6]}...{sheet_id[-4:]}"


def _secrets_key_diagnostic() -> list[str]:
    keys = []
    try:
        keys = list(st.secrets.keys())
    except Exception:
        return []
    return sorted(keys)


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _map_header(value: str) -> str | None:
    normalized = _normalize_header(value)
    if not normalized:
        return None
    if normalized in { _normalize_header(c) for c in MARKET_CACHE_COLUMNS }:
        for col in MARKET_CACHE_COLUMNS:
            if _normalize_header(col) == normalized:
                return col
    return MARKET_CACHE_SYNONYMS.get(normalized)


def _detect_market_cache_header(values: list[list[str]]):
    expected = set(MARKET_CACHE_COLUMNS)
    for idx, row in enumerate(values[:15], start=1):
        mapped = []
        for cell in row:
            mapped_name = _map_header(cell)
            if mapped_name:
                mapped.append(mapped_name)
        found = set(mapped)
        if len(found.intersection(expected)) >= 5:
            header_map = {}
            headers = []
            for col_idx, cell in enumerate(row):
                mapped_name = _map_header(cell)
                headers.append(cell)
                if mapped_name and mapped_name not in header_map.values():
                    header_map[col_idx] = mapped_name
            return idx, headers, header_map
    return None, [], {}


def _map_inputs_header(value: str) -> str | None:
    normalized = _normalize_header(value)
    if not normalized:
        return None
    return INPUTS_MASTER_SYNONYMS.get(normalized)


def _detect_inputs_master_header(values: list[list[str]]):
    expected = {"Symbol", "Market", "CCY"}
    for idx, row in enumerate(values[:15], start=1):
        mapped = []
        for cell in row:
            mapped_name = _map_inputs_header(cell)
            if mapped_name:
                mapped.append(mapped_name)
        found = set(mapped)
        if len(found.intersection(expected)) >= 2:
            header_map = {}
            headers = []
            for col_idx, cell in enumerate(row):
                mapped_name = _map_inputs_header(cell)
                headers.append(cell)
                if mapped_name and mapped_name not in header_map.values():
                    header_map[col_idx] = mapped_name
            return idx, headers, header_map
    return None, [], {}


def _repair_market_cache_sheet(sheet_id: str):
    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    try:
        ws = ss.worksheet(MARKET_CACHE_TAB)
    except Exception:
        ws = ss.add_worksheet(title=MARKET_CACHE_TAB, rows=500, cols=len(MARKET_CACHE_COLUMNS) + 5)
    if ws.col_count < len(MARKET_CACHE_COLUMNS):
        ws.resize(cols=len(MARKET_CACHE_COLUMNS))
    ws.update(range_name="A1", values=[MARKET_CACHE_COLUMNS])
    ws.freeze(rows=1)
    return ws


def _ensure_market_cache_schema(ws, values: list[list[str]]):
    if ws.col_count < len(MARKET_CACHE_COLUMNS):
        ws.resize(cols=len(MARKET_CACHE_COLUMNS))

    header_row, headers, header_map = _detect_market_cache_header(values)
    if header_row:
        old_headers = headers
        data_start = header_row + 1
        data_rows = values[data_start - 1 :]
    else:
        old_headers = values[0] if values else []
        data_rows = values[1:] if values else []

    old_to_new = {}
    for idx, header in enumerate(old_headers):
        mapped = _map_header(header)
        if mapped:
            old_to_new[idx] = mapped

    has_all = all(col in old_to_new.values() for col in MARKET_CACHE_COLUMNS)
    if has_all and header_row == 1:
        return values, header_row, header_map

    new_rows = [MARKET_CACHE_COLUMNS]
    for row in data_rows:
        new_row = [""] * len(MARKET_CACHE_COLUMNS)
        for idx, value in enumerate(row):
            col_name = old_to_new.get(idx)
            if not col_name:
                continue
            new_idx = MARKET_CACHE_COLUMNS.index(col_name)
            new_row[new_idx] = value
        if any(str(cell).strip() for cell in new_row):
            new_rows.append(new_row)

    ws.update(range_name="A1", values=new_rows)
    ws.freeze(rows=1)
    return new_rows, 1, {i: name for i, name in enumerate(MARKET_CACHE_COLUMNS)}


def _load_inputs_master(sheet_id: str) -> list[dict]:
    try:
        client = get_gspread_client()
        ws = client.open_by_key(sheet_id).worksheet(INPUTS_MASTER_TAB)
        values = ws.get_all_values()
    except Exception:
        return []
    if not values:
        return []

    header_row, headers, header_map = _detect_inputs_master_header(values)
    if not header_row:
        return []
    data_start = header_row + 1

    rows = []
    for row in values[data_start - 1 :]:
        record = {}
        for idx, col_name in header_map.items():
            if idx < len(row):
                record[col_name] = row[idx]
        symbol = str(record.get("Symbol", "")).strip().upper()
        market = str(record.get("Market", "")).strip().upper()
        if symbol:
            record["Symbol"] = symbol
            record["Market"] = market
            rows.append(record)
    return rows


@st.cache_data(ttl=3600)
def _load_nzx_fallback_prices() -> dict[str, float]:
    sheet_id = str(st.secrets.get("nzx_quotes_sheet_id", "")).strip()
    tab = str(st.secrets.get("nzx_quotes_tab", "NZX_QUOTES")).strip()
    if not sheet_id:
        return {}

    client = get_gspread_client()
    ws = client.open_by_key(sheet_id).worksheet(tab)
    values = ws.get_all_values()
    if not values:
        return {}

    headers = [str(h).strip() for h in values[0]]
    df = pd.DataFrame(values[1:], columns=headers)
    if df.empty:
        return {}

    ticker_col = next((c for c in headers if c.lower() in {"ticker", "code", "symbol"}), None)
    price_col = next(
        (c for c in headers if c.lower() in {"price", "last", "last price", "last_price", "headline price"}),
        None,
    )
    if not ticker_col or not price_col:
        return {}

    prices = {}
    for _, row in df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip().upper()
        price = _parse_price(row.get(price_col))
        if ticker and price is not None:
            prices[ticker] = price
    return prices


@st.cache_data(ttl=60 * 3060)
def _fetch_quotes(rows: list[dict], nzx_fallback: dict[str, float], refresh_token: float):
    quotes = {}
    warnings = []
    for row in rows:
        ticker = str(row.get("ticker", "")).strip().upper()
        market = str(row.get("market", "")).strip().upper()
        exchange = str(row.get("exchange", "")).strip().upper()
        if not ticker or not market:
            continue
        try:
            is_nz = (
                exchange == "NZX"
                or market in {"NZ", "NZX"}
                or ticker.startswith("NZX:")
            )
            symbol = ticker.replace("NZX:", "") if ticker.startswith("NZX:") else ticker
            if not is_nz and market == "US":
                quote = fetch_us_quote(ticker, exchange=exchange or None)
            elif is_nz:
                quote = fetch_nz_quote(symbol, fallback_prices=nzx_fallback)
            else:
                warnings.append(f"{ticker}: unsupported market '{market}'.")
                continue
        except Exception as exc:
            warnings.append(f"{ticker}: quote fetch failed ({exc}).")
            continue

        if quote.price is None:
            warnings.append(f"{ticker}: price not found from {quote.source}.")
        if market == "NZ" and quote.shares_outstanding is None and _should_overwrite(row.get("shares_out")):
            warnings.append(f"{ticker}: NZX missing Securities Issued; shares_out not updated.")

        quotes[(ticker, market)] = quote
    return quotes, warnings


def _require_market_cache_sheet():
    sheet_id = _get_secret(SECRET_KEY_CANDIDATES)
    if not sheet_id:
        st.warning(
            "Return Ladder Sheet ID missing. Set `return_ladder_sheet_id` (flat) or "
            "`google_sheets.return_ladder_sheet_id` (nested) in Streamlit secrets."
        )
        raise RuntimeError("return_ladder_sheet_id missing")

    try:
        client = get_gspread_client()
    except Exception as exc:
        st.error(f"Failed to load service account credentials: {exc}")
        raise

    try:
        ws = client.open_by_key(sheet_id).worksheet(MARKET_CACHE_TAB)
    except Exception:
        ws = _repair_market_cache_sheet(sheet_id)

    values = ws.get_all_values()
    if not values:
        st.error(f"{MARKET_CACHE_TAB} is empty or missing header row.")
        raise RuntimeError("MARKET_CACHE missing header")

    values, header_row, header_map = _ensure_market_cache_schema(ws, values)
    headers = MARKET_CACHE_COLUMNS
    missing = [c for c in REQUIRED_MARKET_CACHE_COLUMNS if c not in header_map.values()]
    if missing:
        st.error(f"{MARKET_CACHE_TAB} missing columns: {', '.join(missing)}")
        raise RuntimeError("MARKET_CACHE columns missing")

    return sheet_id, ws, headers, values, header_row, header_map


def _build_market_cache_rows(df: pd.DataFrame, cache_map: dict) -> list[dict]:
    rows = []
    for _, row in df.iterrows():
        symbol = str(row.get("ticker", "")).strip().upper()
        market = str(row.get("market", "")).strip().upper()
        if not symbol or not market:
            continue

        ccy = row.get("net_cash_currency") or ("USD" if market == "US" else "NZD")
        price = row.get("current_price")
        shares_out = row.get("shares_out")
        cache_row = cache_map.get((symbol, market), {})
        net_cash = row.get("net_cash_or_debt")
        cfo_bn = row.get("cfo_bn")
        capex_bn = row.get("capex_bn")
        fcf_bn = row.get("fcf_bn")
        fcf1_val = row.get("fcf1")
        if _is_blank_or_zero(fcf1_val):
            fcf1_bn = row.get("fcf1_bn")
            if not _is_blank_or_zero(fcf1_bn):
                try:
                    fcf1_val = float(fcf1_bn) * 1e9
                except (TypeError, ValueError):
                    fcf1_val = None
        g_5y = row.get("g_5y")
        g_terminal = row.get("g_terminal")
        years = row.get("years_to_exit")
        exit_multiple = row.get("exit_multiple")

        if fcf_bn in (None, "", 0, 0.0):
            fcf_val = row.get("fcf_year0") or 0.0
            fcf_bn = fcf_val / 1e9 if fcf_val else 0.0

        if market == "NZ" and cache_row.get("NetCash_bn"):
            net_cash = float(cache_row.get("NetCash_bn")) * 1e9
        if net_cash is None or net_cash == "":
            net_cash = None

        source = row.get("quote_source") or "manual"
        updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows.append(
            {
                "Symbol": symbol,
                "Market": market,
                "CCY": ccy,
                "Price": _blank_if_zero(price),
                "Shares_bn": _blank_if_zero((shares_out / 1e9) if shares_out else 0.0),
                "NetCash_bn": _blank_if_zero((net_cash / 1e9) if net_cash else 0.0),
                "CFO_bn": _blank_if_zero(cfo_bn),
                "Capex_bn": _blank_if_zero(capex_bn),
                "FCF_bn": _blank_if_zero(fcf_bn),
                "FCF1_bn": _blank_if_zero((float(fcf1_val) / 1e9) if not _is_blank_or_zero(fcf1_val) else 0.0),
                "G_5Y": _blank_if_zero(g_5y),
                "G_Terminal": _blank_if_zero(g_terminal),
                "Years": _blank_if_zero(years),
                "Exit_Multiple": _blank_if_zero(exit_multiple),
                "UpdatedAt_UTC": updated_at,
                "Source": source,
                "__preserve_price_shares": source == "nzx_sheet",
            }
        )
    return rows


def _upsert_market_cache(rows: list[dict]):
    debug_payload = bool(st.session_state.get("debug_sheet_payload"))
    if debug_payload and rows:
        payload_df = pd.DataFrame(rows)
        print("MARKET_CACHE payload dtypes:", payload_df.dtypes.to_dict())
        print("MARKET_CACHE payload sample:", payload_df.head(3).to_dict(orient="records"))
        invalid_cols = [
            col for col in payload_df.columns
            if any(_is_invalid_number(value) for value in payload_df[col].tolist())
        ]
        if invalid_cols:
            print("MARKET_CACHE payload invalid columns:", invalid_cols)

    sheet_id, ws, headers, values, header_row, header_map = _require_market_cache_sheet()
    data_start = header_row + 1
    existing = {}
    symbol_idx = next((i for i, c in header_map.items() if c == "Symbol"), None)
    market_idx = next((i for i, c in header_map.items() if c == "Market"), None)
    for idx, row in enumerate(values[data_start - 1 :], start=data_start):
        symbol = str(row[symbol_idx]).strip().upper() if symbol_idx is not None and len(row) > symbol_idx else ""
        market = str(row[market_idx]).strip().upper() if market_idx is not None and len(row) > market_idx else ""
        if symbol and market:
            existing[(symbol, market)] = idx

    logged_invalid = False
    for row in rows:
        key = (row["Symbol"], row["Market"])
        if key in existing:
            row_idx = existing[key]
            existing_row = values[row_idx - 1] if len(values) >= row_idx else []
            row_values = list(existing_row) + [""] * (len(headers) - len(existing_row))
            for col_idx, col_name in header_map.items():
                if col_name in row:
                    if row.get("__preserve_price_shares") and col_name in {"Price", "Shares_bn"}:
                        continue
                    existing_value = row_values[col_idx]
                    if _is_blank_or_zero(row[col_name]) and str(existing_value).strip() != "":
                        continue
                    row_values[col_idx] = row[col_name]
            if debug_payload and not logged_invalid:
                for col_idx, col_name in header_map.items():
                    if col_idx < len(row_values) and _is_invalid_number(row_values[col_idx]):
                        msg = (
                            f"MARKET_CACHE payload invalid value at row {row_idx} "
                            f"col {col_name}: {row_values[col_idx]!r}"
                        )
                        print(msg)
                        st.warning(msg)
                        logged_invalid = True
                        break
            row_values = _sanitize_sheet_row(row_values)
            start = rowcol_to_a1(row_idx, 1)
            end = rowcol_to_a1(row_idx, len(headers))
            ws.update(f"{start}:{end}", [row_values])
        else:
            row_values = [""] * len(headers)
            for col_idx, col_name in header_map.items():
                if col_name in row:
                    if row.get("__preserve_price_shares") and col_name in {"Price", "Shares_bn"}:
                        continue
                    row_values[col_idx] = row[col_name]
            if debug_payload and not logged_invalid:
                for col_idx, col_name in header_map.items():
                    if col_idx < len(row_values) and _is_invalid_number(row_values[col_idx]):
                        msg = (
                            f"MARKET_CACHE payload invalid value at new row "
                            f"col {col_name}: {row_values[col_idx]!r}"
                        )
                        print(msg)
                        st.warning(msg)
                        logged_invalid = True
                        break
            row_values = _sanitize_sheet_row(row_values)
            ws.append_row(row_values, value_input_option="USER_ENTERED")


@st.cache_data(ttl=600)
def _load_market_cache(refresh_token: float) -> pd.DataFrame:
    _, ws, headers, values, header_row, header_map = _require_market_cache_sheet()
    data_start = header_row + 1
    if not values or len(values) < data_start:
        return pd.DataFrame()

    rows = []
    for row in values[data_start - 1 :]:
        record = {col: "" for col in MARKET_CACHE_COLUMNS}
        for idx, col_name in header_map.items():
            if idx < len(row):
                record[col_name] = row[idx]
        rows.append(record)
    return pd.DataFrame(rows, columns=MARKET_CACHE_COLUMNS)


def _test_market_cache_write():
    sheet_id, ws, headers, values, header_row, header_map = _require_market_cache_sheet()
    updated_idx = next((i for i, c in header_map.items() if c == "UpdatedAt_UTC"), None)
    if updated_idx is None:
        raise RuntimeError("UpdatedAt_UTC column not found")

    data_start = header_row + 1
    if len(values) >= data_start:
        ws.update_cell(data_start, updated_idx + 1, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return

    test_row = [""] * len(headers)
    for col_idx, col_name in header_map.items():
        if col_name == "Symbol":
            test_row[col_idx] = "TEST"
        elif col_name == "Market":
            test_row[col_idx] = "US"
        elif col_name == "CCY":
            test_row[col_idx] = "USD"
        elif col_name == "UpdatedAt_UTC":
            test_row[col_idx] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif col_name == "Source":
            test_row[col_idx] = "debug"
    ws.append_row(test_row, value_input_option="USER_ENTERED")

def _default_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "market": "US",
                "exchange": "NASDAQ",
                "current_price": 0.0,
                "shares_out": 0.0,
                "net_cash_or_debt": 0.0,
                "fcf_year0": 100000000.0,
                "fcf1": 100000000.0,
                "cfo_bn": 0.0,
                "capex_bn": 0.0,
                "fcf_bn": 0.0,
                "years_to_exit": 5,
                "exit_multiple": 0.0,
                "g_5y": 0.05,
                "g_terminal": 0.02,
                "growth_rate": 0.05,
                "net_cash_source": "",
                "net_cash_currency": "",
                "quote_source": "",
            },
            {
                "ticker": "IFT",
                "market": "NZ",
                "exchange": "NZX",
                "current_price": 0.0,
                "shares_out": 0.0,
                "net_cash_or_debt": 0.0,
                "fcf_year0": 50000000.0,
                "fcf1": 50000000.0,
                "cfo_bn": 0.0,
                "capex_bn": 0.0,
                "fcf_bn": 0.0,
                "years_to_exit": 5,
                "exit_multiple": 0.0,
                "g_5y": 0.04,
                "g_terminal": 0.02,
                "growth_rate": 0.04,
                "net_cash_source": "",
                "net_cash_currency": "",
                "quote_source": "",
            },
        ]
    )


st.sidebar.header("DCF Settings")
required_returns_input = st.sidebar.text_input(
    "Required Returns (comma separated)",
    value=", ".join(str(r) for r in DEFAULT_REQUIRED_RETURNS),
)
required_returns = _parse_required_returns(required_returns_input)
base_return = st.sidebar.selectbox(
    "Base Return for Upside",
    required_returns,
    index=required_returns.index(0.10) if 0.10 in required_returns else 0,
)
zone_green = st.sidebar.number_input("Green Zone Threshold (+%)", value=20.0, step=5.0) / 100
zone_red = st.sidebar.number_input("Red Zone Threshold (-%)", value=-20.0, step=5.0) / 100

st.sidebar.header("Quotes")
refresh_scope = st.sidebar.radio(
    "Refresh scope",
    ["INPUTS_MASTER (sheet)", "Inputs only"],
    index=0,
)
refresh = st.sidebar.button("Refresh Quotes")
if refresh:
    st.session_state["quotes_refresh_token"] = time.time()
    st.session_state["market_cache_refresh"] = True
    st.rerun()

refresh_token = st.session_state.get("quotes_refresh_token", 0.0)

st.sidebar.subheader("Market Cache Status")
sheet_id = _get_secret(SECRET_KEY_CANDIDATES)
if not sheet_id:
    st.sidebar.warning(
        "Sheet ID missing. Expected `return_ladder_sheet_id` (flat) or "
        "`google_sheets.return_ladder_sheet_id` (nested)."
    )
    st.sidebar.text_input(
        "Return Ladder Sheet ID (override)",
        key="return_ladder_sheet_id_override",
        help="Temporary override if secrets are missing.",
    )
else:
    try:
        _, _, _, values, header_row, _ = _require_market_cache_sheet()
        row_count = max(len(values) - header_row, 0)
        st.sidebar.success(
            f"MARKET_CACHE ready (row {header_row} header, {row_count} rows, {_mask_sheet_id(sheet_id)})"
        )
    except Exception as exc:
        st.sidebar.error(f"MARKET_CACHE error: {exc}")

with st.sidebar.expander("Secrets Diagnostic", expanded=False):
    keys = _secrets_key_diagnostic()
    st.write("Secrets keys:", keys if keys else "Unavailable")

with st.expander("MARKET_CACHE Debug", expanded=False):
    st.write("Sheet ID:", _mask_sheet_id(sheet_id) if sheet_id else "Missing")
    st.write("Tabs read:", [INPUTS_MASTER_TAB, MARKET_CACHE_TAB])
    st.write("Tab write:", [MARKET_CACHE_TAB])
    if sheet_id:
        try:
            client = get_gspread_client()
            ss = client.open_by_key(sheet_id)
            st.write("Worksheets:", [ws.title for ws in ss.worksheets()])
            ws = ss.worksheet(MARKET_CACHE_TAB)
            values = ws.get_all_values()
            header_row, headers, header_map = _detect_market_cache_header(values)
            st.write("Header row:", header_row or "Not found")
            normalized_headers = [_normalize_header(h) for h in headers] if headers else []
            st.write("Normalized headers:", normalized_headers)
            preview = values[:12] if values else []
            st.write("Preview rows (1-12):", preview)
            st.write("INPUTS_MASTER rows:", len(_load_inputs_master(sheet_id)))
        except Exception as exc:
            st.write(f"Debug error: {exc}")

with st.expander("Return Ladder README", expanded=False):
    st.markdown(
        """
        **How to configure**
        - Secret key: `return_ladder_sheet_id` (preferred) or nested `google_sheets.return_ladder_sheet_id`.
        - Required tabs: `INPUTS_MASTER` and `MARKET_CACHE`.
        - MARKET_CACHE headers: `Symbol, Market, CCY, Price, Shares_bn, NetCash_bn, CFO_bn, Capex_bn, FCF_bn, UpdatedAt_UTC, Source`.
        """
    )


@st.cache_data(ttl=3600)
def _load_sec_tickers():
    return fetch_sec_company_tickers()


@st.cache_data(ttl=3600)
def _load_sec_companyfacts(cik: str):
    return fetch_sec_companyfacts(cik)

st.subheader("Inputs")
if "return_ladder_rows" not in st.session_state:
    st.session_state["return_ladder_rows"] = _default_rows()

edited = st.data_editor(
    st.session_state["return_ladder_rows"],
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "market": st.column_config.SelectboxColumn("market", options=["US", "NZ"]),
        "current_price": st.column_config.NumberColumn(format="%.2f"),
        "shares_out": st.column_config.NumberColumn(format="%.0f"),
        "net_cash_or_debt": st.column_config.NumberColumn(format="%.0f"),
        "fcf_year0": st.column_config.NumberColumn(format="%.0f"),
        "fcf1": st.column_config.NumberColumn(format="%.0f"),
        "cfo_bn": st.column_config.NumberColumn(format="%.3f"),
        "capex_bn": st.column_config.NumberColumn(format="%.3f"),
        "fcf_bn": st.column_config.NumberColumn(format="%.3f"),
        "g_5y": st.column_config.NumberColumn(format="%.2%"),
        "g_terminal": st.column_config.NumberColumn(format="%.2%"),
        "growth_rate": st.column_config.NumberColumn(format="%.2%"),
        "exit_multiple": st.column_config.NumberColumn(format="%.2f"),
        "net_cash_source": st.column_config.TextColumn("net_cash_source", disabled=True),
        "net_cash_currency": st.column_config.TextColumn("net_cash_currency", disabled=True),
    },
)
edited = coerce_inputs_df(edited)
st.session_state["return_ladder_rows"] = edited

rows = edited.to_dict(orient="records")
nzx_fallback = _load_nzx_fallback_prices()
cache_map = {}
inputs_master_rows = []
try:
    market_cache_df = _load_market_cache(refresh_token)
    if not market_cache_df.empty:
        market_cache_df["Symbol"] = market_cache_df["Symbol"].astype(str).str.upper()
        market_cache_df["Market"] = market_cache_df["Market"].astype(str).str.upper()
        cache_map = {
            (row["Symbol"], row["Market"]): row
            for _, row in market_cache_df.iterrows()
            if row.get("Symbol") and row.get("Market")
        }
    sheet_id = _get_secret(SECRET_KEY_CANDIDATES)
    if sheet_id:
        inputs_master_rows = _load_inputs_master(sheet_id)
except Exception:
    market_cache_df = pd.DataFrame()

inputs_map = {}
for row in rows:
    key = (str(row.get("ticker", "")).strip().upper(), str(row.get("market", "")).strip().upper())
    if key[0] and key[1]:
        inputs_map[key] = row

union_rows = []
if refresh_scope == "Inputs only" or not inputs_master_rows:
    union_rows = list(inputs_map.values())
else:
    for master_row in inputs_master_rows:
        symbol = master_row.get("Symbol")
        cache_row = cache_map.get((symbol, str(master_row.get("Market") or "").strip().upper()), {})
        market = str(master_row.get("Market") or cache_row.get("Market") or "").strip().upper()
        key = (symbol, market)
        cache_row = cache_map.get(key, cache_row)
        ccy = str(master_row.get("CCY") or cache_row.get("CCY") or ("USD" if market == "US" else "NZD")).strip().upper()
        base_row = {
            "ticker": symbol,
            "market": market,
            "exchange": "",
            "current_price": cache_row.get("Price") or 0.0,
            "shares_out": float(cache_row.get("Shares_bn") or 0) * 1e9,
            "net_cash_or_debt": float(cache_row.get("NetCash_bn") or 0) * 1e9,
            "fcf_year0": float(cache_row.get("FCF_bn") or 0) * 1e9,
            "fcf1": float(cache_row.get("FCF1_bn") or 0) * 1e9,
            "g_5y": _parse_rate(cache_row.get("G_5Y")),
            "g_terminal": _parse_rate(cache_row.get("G_Terminal")),
            "years_to_exit": int(float(cache_row.get("Years") or 0)) if str(cache_row.get("Years") or "").strip() else 0,
            "exit_multiple": float(cache_row.get("Exit_Multiple") or 0),
            "quote_source": cache_row.get("Source") or "",
            "net_cash_currency": ccy,
        }
        if master_row.get("NetCash_bn") not in (None, "", 0, 0.0):
            base_row["net_cash_or_debt"] = float(master_row.get("NetCash_bn") or 0) * 1e9
        if master_row.get("FCF1_bn") not in (None, "", 0, 0.0):
            base_row["fcf1"] = float(master_row.get("FCF1_bn") or 0) * 1e9
        if master_row.get("G_5Y") not in (None, "", 0, 0.0):
            base_row["g_5y"] = _parse_rate(master_row.get("G_5Y"))
        if master_row.get("G_Terminal") not in (None, "", 0, 0.0):
            base_row["g_terminal"] = _parse_rate(master_row.get("G_Terminal"))
        if master_row.get("Years") not in (None, "", 0, 0.0):
            base_row["years_to_exit"] = int(float(master_row.get("Years") or 0))
        if master_row.get("Exit_Multiple") not in (None, "", 0, 0.0):
            base_row["exit_multiple"] = float(master_row.get("Exit_Multiple") or 0)
        inputs_row = inputs_map.get(key)
        if inputs_row:
            for field, value in inputs_row.items():
                if value not in (None, "", 0, 0.0):
                    base_row[field] = value
        union_rows.append(base_row)

quotes, quote_warnings = _fetch_quotes(union_rows, nzx_fallback, refresh_token)

if quote_warnings:
    st.warning("Quote issues:\n" + "\n".join(f"- {w}" for w in quote_warnings))

updated_rows = []
refresh_debug = []
for row in union_rows:
    ticker = str(row.get("ticker", "")).strip().upper()
    market = str(row.get("market", "")).strip().upper()
    quote = quotes.get((ticker, market))
    if quote:
        row["quote_source"] = quote.source
        if quote.price is not None and _should_overwrite(row.get("current_price")):
            row["current_price"] = quote.price
        if quote.shares_outstanding and _should_overwrite(row.get("shares_out")):
            row["shares_out"] = float(quote.shares_outstanding)
        refresh_debug.append(
            {
                "Ticker": ticker,
                "Market": market,
                "Provider": quote.source,
                "URL": quote.url,
                "Status": quote.status_code,
                "Raw Price": quote.raw_price,
                "Parsed Price": quote.price,
                "Raw Shares": quote.raw_shares,
                "Parsed Shares": quote.shares_outstanding,
                "Snippet": (quote.error_snippet or "")[:200],
            }
        )
    else:
        refresh_debug.append(
            {
                "Ticker": ticker,
                "Market": market,
                "Provider": "none",
                "URL": "",
                "Status": "",
                "Raw Price": "",
                "Parsed Price": "",
                "Raw Shares": "",
                "Parsed Shares": "",
                "Snippet": "",
            }
        )
    updated_rows.append(row)

updated_df = coerce_inputs_df(pd.DataFrame(updated_rows))
st.session_state["return_ladder_rows"] = updated_df

fundamentals_warnings = []
try:
    sec_tickers = _load_sec_tickers()
except Exception as exc:
    sec_tickers = None
    fundamentals_warnings.append(f"SEC ticker list unavailable: {exc}")

final_rows = []
for row in updated_df.to_dict(orient="records"):
    ticker = str(row.get("ticker", "")).strip().upper()
    market = str(row.get("market", "")).strip().upper()
    if market != "US" or not ticker:
        if market == "NZ" and _should_overwrite(row.get("net_cash_or_debt")):
            fundamentals_warnings.append(
                f"{ticker}: Net cash/debt requires statements data; enter manually for NZ tickers."
            )
        final_rows.append(row)
        continue

    if not _needs_fundamentals(row):
        final_rows.append(row)
        continue

    if sec_tickers is None:
        final_rows.append(row)
        continue

    cik = get_cik_for_ticker(ticker, sec_tickers)
    if not cik:
        fundamentals_warnings.append(f"{ticker}: SEC CIK not found.")
        final_rows.append(row)
        continue

    try:
        facts = _load_sec_companyfacts(cik)
        net_cash, currency, net_cash_detail = compute_net_cash_from_companyfacts_detailed(facts)
        cfo, capex, fcf, cf_currency, cf_detail = compute_cashflow_from_companyfacts_detailed(facts)
        sec_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"

        if net_cash is None or net_cash == 0:
            fundamentals_warnings.append(
                f"{ticker}: Net cash/debt missing from SEC facts "
                f"(url={sec_url}; cash_tag={net_cash_detail.get('cash_tag')}; "
                f"inv_tag={net_cash_detail.get('inv_tag')}; "
                f"debt_current_tag={net_cash_detail.get('debt_current_tag')}; "
                f"debt_long_tag={net_cash_detail.get('debt_long_tag')})."
            )
        elif _should_overwrite(row.get("net_cash_or_debt")):
            row["net_cash_or_debt"] = float(net_cash)
            row["net_cash_source"] = "sec_xbrl"
            row["net_cash_currency"] = currency or "USD"

        if cfo is None or cfo == 0:
            fundamentals_warnings.append(
                f"{ticker}: CFO missing from SEC facts "
                f"(url={sec_url}; cfo_tag={cf_detail.get('cfo_tag')})."
            )
        elif _should_overwrite(row.get("cfo_bn")):
            row["cfo_bn"] = float(cfo) / 1e9

        if capex is None or capex == 0:
            fundamentals_warnings.append(
                f"{ticker}: Capex missing from SEC facts "
                f"(url={sec_url}; capex_tag={cf_detail.get('capex_tag')})."
            )
        elif _should_overwrite(row.get("capex_bn")):
            row["capex_bn"] = float(capex) / 1e9

        if fcf is None or fcf == 0:
            fundamentals_warnings.append(
                f"{ticker}: FCF missing from SEC facts "
                f"(url={sec_url}; cfo_tag={cf_detail.get('cfo_tag')}; "
                f"capex_tag={cf_detail.get('capex_tag')})."
            )
        else:
            if _should_overwrite(row.get("fcf_bn")):
                row["fcf_bn"] = float(fcf) / 1e9
            if _should_overwrite(row.get("fcf_year0")):
                row["fcf_year0"] = float(fcf)
    except Exception as exc:
        fundamentals_warnings.append(f"{ticker}: SEC facts fetch failed ({exc}).")

    final_rows.append(row)

final_df = coerce_inputs_df(pd.DataFrame(final_rows))
st.session_state["return_ladder_rows"] = final_df

if fundamentals_warnings:
    st.warning("Fundamentals warnings:\n" + "\n".join(f"- {w}" for w in fundamentals_warnings))

net_cash_warnings = []
for row in final_df.to_dict(orient="records"):
    ticker = str(row.get("ticker", "")).strip().upper()
    if not ticker:
        continue
    if _should_overwrite(row.get("net_cash_or_debt")):
        net_cash_warnings.append(f"{ticker}: Net cash/debt missing; defaulting NetCash_bn to 0.")

if net_cash_warnings:
    st.warning("Net cash warnings:\n" + "\n".join(f"- {w}" for w in net_cash_warnings))

dcf_input_warnings = []
if dcf_input_warnings:
    st.warning("DCF input warnings:\n" + "\n".join(f"- {w}" for w in dcf_input_warnings))

if st.sidebar.checkbox("Show debug"):
    st.subheader("Refresh Debug")
    st.dataframe(pd.DataFrame(refresh_debug), use_container_width=True)

debug_sheet_payload = st.sidebar.checkbox("Debug sheet payload")
st.session_state["debug_sheet_payload"] = debug_sheet_payload

debug_dcf_inputs = st.sidebar.checkbox("Debug DCF inputs (PYPL)")
st.session_state["debug_dcf_inputs"] = debug_dcf_inputs

with st.expander("Quote Diagnostics", expanded=False):
    st.dataframe(pd.DataFrame(refresh_debug), use_container_width=True)

if st.sidebar.button("Test Sheets Write"):
    try:
        _test_market_cache_write()
        st.sidebar.success(f"Test write succeeded ({_mask_sheet_id(sheet_id)})")
    except Exception as exc:
        st.sidebar.error(f"Test write failed ({_mask_sheet_id(sheet_id)}): {exc}")
        print(f"Test write failed: {exc}")

if st.sidebar.button("Repair MARKET_CACHE Sheet"):
    try:
        sheet_id = _get_secret(SECRET_KEY_CANDIDATES)
        if not sheet_id:
            raise RuntimeError("return_ladder_sheet_id missing")
        _repair_market_cache_sheet(sheet_id)
        st.sidebar.success("MARKET_CACHE repaired.")
    except Exception as exc:
        st.sidebar.error(f"MARKET_CACHE repair failed: {exc}")
        print(f"MARKET_CACHE repair failed: {exc}")

if st.session_state.pop("market_cache_refresh", False):
    try:
        if not sheet_id:
            raise RuntimeError("return_ladder_sheet_id missing")
        cache_rows = _build_market_cache_rows(final_df, cache_map)
        _upsert_market_cache(cache_rows)
        st.success("MARKET_CACHE updated.")
    except Exception as exc:
        st.error(f"MARKET_CACHE update failed: {exc}")
        print(f"MARKET_CACHE update failed: {exc}")

try:
    market_cache_df = _load_market_cache(refresh_token)
except Exception as exc:
    st.error(f"MARKET_CACHE load failed: {exc}")
    market_cache_df = pd.DataFrame()
if not market_cache_df.empty:
    st.subheader("Market Cache (Sheet)")
    price_ok = market_cache_df["Price"].astype(str).str.strip().ne("")
    shares_ok = market_cache_df["Shares_bn"].astype(str).str.strip().ne("")
    st.dataframe(market_cache_df[price_ok & shares_ok], use_container_width=True)
    market_cache_df["Symbol"] = market_cache_df["Symbol"].astype(str).str.upper()
    market_cache_df["Market"] = market_cache_df["Market"].astype(str).str.upper()
    cache_map = {
        (row["Symbol"], row["Market"]): row
        for _, row in market_cache_df.iterrows()
        if row.get("Symbol") and row.get("Market")
    }
    synced_rows = []
    for row in final_df.to_dict(orient="records"):
        key = (str(row.get("ticker", "")).strip().upper(), str(row.get("market", "")).strip().upper())
        cache_row = cache_map.get(key)
        if cache_row is not None:
            row["current_price"] = float(cache_row.get("Price") or row.get("current_price") or 0)
            row["shares_out"] = float(cache_row.get("Shares_bn") or 0) * 1e9
            row["net_cash_or_debt"] = float(cache_row.get("NetCash_bn") or 0) * 1e9
            row["fcf_year0"] = float(cache_row.get("FCF_bn") or 0) * 1e9
            row["fcf1"] = float(cache_row.get("FCF1_bn") or 0) * 1e9
            row["g_5y"] = _parse_rate(cache_row.get("G_5Y"))
            row["g_terminal"] = _parse_rate(cache_row.get("G_Terminal"))
            row["years_to_exit"] = int(float(cache_row.get("Years") or 0)) if str(cache_row.get("Years") or "").strip() else row.get("years_to_exit")
            row["exit_multiple"] = float(cache_row.get("Exit_Multiple") or row.get("exit_multiple") or 0)
        synced_rows.append(row)

    known_keys = {(r.get("ticker", "").strip().upper(), r.get("market", "").strip().upper()) for r in synced_rows}
    for (symbol, market), cache_row in cache_map.items():
        if (symbol, market) in known_keys:
            continue
        if str(cache_row.get("Price", "")).strip() and str(cache_row.get("Shares_bn", "")).strip():
            synced_rows.append(
                {
                    "ticker": symbol,
                    "market": market,
                    "exchange": "",
                    "current_price": float(cache_row.get("Price") or 0),
                    "shares_out": float(cache_row.get("Shares_bn") or 0) * 1e9,
                    "net_cash_or_debt": float(cache_row.get("NetCash_bn") or 0) * 1e9,
                    "fcf_year0": float(cache_row.get("FCF_bn") or 0) * 1e9,
                    "fcf1": float(cache_row.get("FCF1_bn") or 0) * 1e9,
                    "g_5y": _parse_rate(cache_row.get("G_5Y")),
                    "g_terminal": _parse_rate(cache_row.get("G_Terminal")),
                    "years_to_exit": int(float(cache_row.get("Years") or 0)) if str(cache_row.get("Years") or "").strip() else 5,
                    "exit_multiple": float(cache_row.get("Exit_Multiple") or 0.0),
                    "growth_rate": _parse_rate(cache_row.get("G_5Y")),
                    "net_cash_currency": cache_row.get("CCY") or ("USD" if market == "US" else "NZD"),
                }
            )
    final_df = coerce_inputs_df(pd.DataFrame(synced_rows))

summary_rows = []
results = {}
errors = []

row_errors, row_warnings = validate_rows(final_df.to_dict(orient="records"))
if row_warnings:
    st.warning("Warnings:\n" + "\n".join(f"- {w}" for w in row_warnings))
error_tickers = {msg.split(":", 1)[0] for msg in row_errors}

for row in final_df.to_dict(orient="records"):
    ticker = str(row.get("ticker", "")).strip().upper()
    market = str(row.get("market", "")).strip().upper()
    if not ticker or not market:
        continue
    if ticker in error_tickers:
        continue

    try:
        fcf1_value = _resolve_fcf1(row, ticker, dcf_input_warnings)
        g_5y = row.get("g_5y")
        if _is_blank_or_zero(g_5y):
            g_5y = row.get("growth_rate")
        g_terminal = row.get("g_terminal")
        if _is_blank_or_zero(g_terminal):
            g_terminal = 0.0
        if _is_blank_or_zero(fcf1_value):
            errors.append(f"{ticker}: fcf1 missing - set FCF1 to match sheet template.")
            continue
        inputs = DCFInputs(
            ticker=ticker,
            market=market,
            current_price=float(row.get("current_price") or 0),
            shares_out=float(row.get("shares_out") or 0),
            net_cash=float(row.get("net_cash_or_debt") or 0),
            fcf1=float(fcf1_value or 0),
            years=int(row.get("years_to_exit") or 0),
            exit_multiple=float(row.get("exit_multiple") or 0),
            growth_rate=float(g_5y or 0),
            terminal_growth_rate=float(g_terminal or 0),
        )
        if debug_dcf_inputs and ticker == "PYPL":
            st.info(
                {
                    "ticker": ticker,
                    "price": inputs.current_price,
                    "shares_out": inputs.shares_out,
                    "net_cash_or_debt": inputs.net_cash,
                    "fcf1_used": inputs.fcf1,
                    "g_5y": inputs.growth_rate,
                    "g_terminal": inputs.terminal_growth_rate,
                    "years": inputs.years,
                    "terminal_method": "perpetuity_growth",
                    "discount_rates": required_returns,
                }
            )
        if inputs.years <= 0:
            errors.append(f"{ticker}: years_to_exit must be greater than 0.")
            continue
        result = build_dcf(inputs, required_returns)
        results[ticker] = result
        summary_rows.append(build_summary_row(inputs, result, base_return, zone_green, zone_red))
    except Exception as exc:
        errors.append(f"{ticker}: {exc}")

if row_errors or errors:
    st.error(
        "Input errors:\n"
        + "\n".join(f"- {e}" for e in (row_errors + errors))
    )

st.subheader("Summary")
if summary_rows:
    summary_df = pd.DataFrame(summary_rows)
    fv_cols = [c for c in summary_df.columns if c.startswith("FV@")]
    display_cols = ["Ticker", "Market", "Current Price"] + fv_cols + ["Upside @ Base", "Zone"]
    summary_df = summary_df[display_cols]
    st.dataframe(
        summary_df.style.format(
            {**{c: "${:,.2f}" for c in fv_cols}, "Current Price": "${:,.2f}", "Upside @ Base": "{:+.1%}"},
            na_rep="N/A",
        ),
        use_container_width=True,
    )
else:
    st.info("Add tickers and refresh quotes to build the summary table.")

st.subheader("DCF Blocks")
for ticker, result in results.items():
    inputs = result.inputs
    with st.expander(f"{ticker} ({inputs.market}) DCF"):
        if inputs.fcf1 < 0:
            st.warning("FCF is negative; PVs and fair values will reflect cash burn.")

        st.dataframe(result.pv_table.style.format("{:,.0f}"), use_container_width=True)

        metric_rows = []
        for required_return in required_returns:
            metric_rows.append(
                {
                    "Required Return": f"{required_return:.0%}",
                    "Enterprise Value": result.enterprise_values[required_return],
                    "Equity Value": result.equity_values[required_return],
                    "Fair Value / Share": result.fair_values[required_return],
                }
            )

        metrics_df = pd.DataFrame(metric_rows)
        st.dataframe(
            metrics_df.style.format(
                {
                    "Enterprise Value": "${:,.0f}",
                    "Equity Value": "${:,.0f}",
                    "Fair Value / Share": "${:,.2f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )

        st.caption(
            f"Terminal method: perpetuity growth | Growth (Y1-5): {inputs.growth_rate:.1%} | "
            f"Terminal g: {inputs.terminal_growth_rate:.1%} | Net cash/debt: {inputs.net_cash:,.0f}"
        )
