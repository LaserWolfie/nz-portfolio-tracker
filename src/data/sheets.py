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
            df[column] = robust_numeric_clean(df, column)
    return df


@st.cache_data(ttl=600)
def load_stock_df():
    client = get_gspread_client()
    sheet = client.open_by_key(STOCKS_SHEET_ID)
    values = sheet.worksheet(STOCKS_TAB).get_all_values()
    headers = [str(h).strip() for h in values[0]]
    df = pd.DataFrame(values[1:], columns=headers)
    return _coerce_numeric_columns(df)


@st.cache_data(ttl=600)
def load_property_df():
    client = get_gspread_client()
    sheet = client.open_by_key(PROPERTY_SHEET_ID)
    values = sheet.worksheet(PROPERTY_TAB).get_all_values()
    headers = [str(h).strip() for h in values[0]]
    df = pd.DataFrame(values[1:], columns=headers)
    df.columns = [c.replace(" ", "_") for c in df.columns]
    return _coerce_numeric_columns(df)


def load_personal_assets_csv():
    if os.path.exists("personal_assets.csv"):
        return pd.read_csv("personal_assets.csv")
    return pd.DataFrame()


def ensure_data_loaded():
    if "stock_df" not in st.session_state or st.session_state.stock_df.empty:
        st.session_state.stock_df = load_stock_df()
    if "prop_df" not in st.session_state or st.session_state.prop_df.empty:
        st.session_state.prop_df = load_property_df()
    if "personal_df" not in st.session_state or st.session_state.personal_df.empty:
        st.session_state.personal_df = load_personal_assets_csv()


def clear_app_caches():
    st.cache_data.clear()
    for key in ("stock_df", "prop_df", "personal_df"):
        if key in st.session_state:
            del st.session_state[key]
