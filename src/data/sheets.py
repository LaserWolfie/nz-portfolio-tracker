import os

import gspread
import pandas as pd
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials

from src.config import (
    PROPERTY_SHEET_ID,
    PROPERTY_TAB,
    STOCKS_SHEET_ID,
    STOCKS_TAB,
)
from src.utils.cleaning import robust_numeric_clean

REQUIRED_PROPERTY_COLS = {"Entity_Name", "Payout_Ratio"}
_DEBUG_PROP_COLS = False


def _normalize_ratio(value):
    if pd.isna(value):
        return pd.NA
    s = str(value).strip()
    if s == "":
        return pd.NA
    has_percent = "%" in s
    s = s.replace("%", "").replace(",", "").strip()
    try:
        num = float(s)
    except Exception:
        return pd.NA
    if has_percent or (num > 1.0 and num <= 100.0):
        return num / 100.0
    return num


@st.cache_resource
def get_gspread_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.path.exists("credentials.json"):
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    elif "gcp_service_account" in st.secrets:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            dict(st.secrets["gcp_service_account"]),
            scope,
        )
    else:
        raise RuntimeError("Connection failed: No credentials found.")
    return gspread.authorize(creds)


def _coerce_numeric_columns(df):
    numeric_columns = [
        "Current_Value",
        "Original_Value",
        "Annual_Distribution",
        "Taxable_Income",
        "Imputation_Credits",
    ]
    for column in numeric_columns:
        if column in df.columns:
            robust_numeric_clean(df, column)
    return df


@st.cache_data(ttl=600)
def load_stock_df() -> pd.DataFrame:
    try:
        client = get_gspread_client()
        ws = client.open_by_key(STOCKS_SHEET_ID).worksheet(STOCKS_TAB)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
    except Exception as exc:
        st.error(f"Stock sheet sync failed: {exc}")
        return pd.DataFrame()

    # Find first non-empty row as header
    header_idx = None
    for i, row in enumerate(values):
        if any(str(cell).strip() for cell in row):
            header_idx = i
            break
    if header_idx is None or header_idx >= len(values) - 1:
        return pd.DataFrame()

    headers = [str(h).strip() for h in values[header_idx]]
    df = pd.DataFrame(values[header_idx + 1 :], columns=headers)

    # Drop fully empty rows
    df = df[~df.apply(lambda r: all(str(x).strip() == "" for x in r), axis=1)].copy()

    # ---- Backward-compatible aliasing (prevents KeyError in pages) ----
    # If your loader previously normalized names, pages may still expect spaced versions.
    alias_candidates = {
        "Purchase Price": ["Purchase_Price", "PurchasePrice", "Avg_Cost", "Average_Cost", "Buy_Price", "Entry_Price", "Cost"],
        "Shares": ["Quantity", "Qty", "Units", "Holding", "Number_of_Shares", "No_of_Shares"],
        "Ticker": ["Symbol", "Code", "Ticker_Symbol", "Instrument", "Security"],
        "Current Price": ["Current_Price", "Price", "Last_Price", "Last", "Close", "Market_Price"],
        "Value": ["Market Value", "Market_Value", "Current Value", "Current_Value", "NZD_Value", "Value_NZD", "MarketValue"]
    }

    # Build a lookup that matches columns case-insensitively and with/without underscores
    def _norm(s: str) -> str:
        return str(s).strip().lower().replace(" ", "_")

    norm_to_actual = {_norm(c): c for c in df.columns}

    def _first_existing(cols: list[str]):
        for c in cols:
            key = _norm(c)
            if key in norm_to_actual:
                return norm_to_actual[key]
        return None

    for canon, alts in alias_candidates.items():
        if canon not in df.columns:
            found = _first_existing([canon] + alts)
            if found is not None:
                df[canon] = df[found]

    # ---- Create/clean a reliable numeric Value column for app totals ----
    # Prefer an existing "Value"-like column, otherwise compute Shares * Current Price.
    value_source = None
    for c in ["Value", "Market Value", "Market_Value", "Current Value", "Current_Value", "NZD_Value", "Value_NZD"]:
        if c in df.columns:
            value_source = c
            break

    if value_source is not None:
        # Strong numeric cleanup for Value-like strings (e.g., "NZD 12,345")
        s = df[value_source].astype(str)
        s = s.str.replace(",", "", regex=False)
        s = s.str.replace(r"[\$,%]", "", regex=True)
        s = s.str.replace(r"[^\d\.\-]", "", regex=True)
        s = s.str.strip().replace(["", "-", ".", "--"], "0")
        df["Value"] = pd.to_numeric(s, errors="coerce").fillna(0.0)
    else:
        # Try compute from Shares * Current Price if possible
        if "Shares" in df.columns and "Current Price" in df.columns:
            df = robust_numeric_clean(df, "Shares")
            df = robust_numeric_clean(df, "Current Price")
            df["Value"] = df["Shares"] * df["Current Price"]
        else:
            # Ensure Value exists so app doesn't crash; it will be 0
            df["Value"] = 0.0

    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    # Also numeric clean key columns used in pages
    if "Shares" in df.columns:
        robust_numeric_clean(df, "Shares")
    if "Purchase Price" in df.columns:
        robust_numeric_clean(df, "Purchase Price")
    if "Current Price" in df.columns:
        robust_numeric_clean(df, "Current Price")

    return df


@st.cache_data(ttl=600)
def load_property_df():
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(PROPERTY_SHEET_ID)
        values = sheet.worksheet(PROPERTY_TAB).get_all_values()
        if not values:
            return pd.DataFrame()
        headers = [str(h).strip() for h in values[0]]
        df = pd.DataFrame(values[1:], columns=headers)
        df.columns = [c.replace(" ", "_") for c in df.columns]
        if "Payout_Ratio" not in df.columns:
            df["Payout_Ratio"] = pd.NA
        df["Payout_Ratio"] = df["Payout_Ratio"].apply(_normalize_ratio)
        return _coerce_numeric_columns(df)
    except Exception as exc:
        st.error(f"Property sheet sync failed: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=900)
def load_signals_df() -> pd.DataFrame:
    """
    Loads portfolio/news signals from the Catalyst Hub Google Sheet.
    Expected columns: Ticker, FeedType, Title, URL, Summary, Source, Published
    """
    sheet_id = str(st.secrets.get("signals_sheet_id", "")).strip()
    tab = str(st.secrets.get("signals_tab", "RAW_SCRIPT")).strip()

    if not sheet_id:
        return pd.DataFrame()

    try:
        client = get_gspread_client()
        ws = client.open_by_key(sheet_id).worksheet(tab)
        values = ws.get_all_values()

        if not values or len(values) < 2:
            return pd.DataFrame()

        # Find first non-empty header row
        header_idx = None
        for i, row in enumerate(values):
            if any(str(c).strip() for c in row):
                header_idx = i
                break
        if header_idx is None or header_idx >= len(values) - 1:
            return pd.DataFrame()

        headers = [str(h).strip() for h in values[header_idx]]
        df = pd.DataFrame(values[header_idx + 1 :], columns=headers)

        # Drop fully empty rows
        df = df[~df.apply(lambda r: all(str(x).strip() == "" for x in r), axis=1)].copy()

        # Remove accidental repeated header rows in body
        if "Ticker" in df.columns:
            df = df[df["Ticker"].astype(str).str.strip().str.upper() != "TICKER"]

        return df

    except Exception:
        # Fail-closed: don't crash the app if the sheet is unreachable
        return pd.DataFrame()


def load_personal_assets_csv():
    if os.path.exists("personal_assets.csv"):
        return pd.read_csv("personal_assets.csv")
    return pd.DataFrame()


def ensure_data_loaded(force_refresh: bool = False):
    if force_refresh:
        st.cache_data.clear()
        load_stock_df.clear()
        load_property_df.clear()
        load_signals_df.clear()
    if "stock_df" not in st.session_state or st.session_state.stock_df.empty:
        st.session_state.stock_df = load_stock_df()
    if "prop_df" not in st.session_state or st.session_state.prop_df.empty:
        st.session_state.prop_df = load_property_df()
    if "prop_df" in st.session_state and not st.session_state.prop_df.empty:
        missing = REQUIRED_PROPERTY_COLS - set(st.session_state.prop_df.columns)
        if missing:
            if _DEBUG_PROP_COLS:
                print(f"[prop_df] Missing columns: {sorted(missing)}")
            st.session_state.prop_df = load_property_df()
    if "signals_df" not in st.session_state or st.session_state.signals_df.empty:
        st.session_state.signals_df = load_signals_df()
    if "personal_df" not in st.session_state or st.session_state.personal_df.empty:
        st.session_state.personal_df = load_personal_assets_csv()


def clear_app_caches():
    st.cache_data.clear()
    st.cache_resource.clear()
    for key in (
        "stock_df",
        "prop_df",
        "personal_df",
        "signals_df",
        "signals_cache",
        "market_context_md",
        "market_context_asof",
    ):
        st.session_state.pop(key, None)
    ensure_data_loaded(force_refresh=True)
