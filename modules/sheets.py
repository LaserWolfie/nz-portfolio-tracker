"""Shared Google Sheets identifiers and connection handling.

Every sheet is opened by key, never by name, so a renamed or duplicated
spreadsheet in Drive cannot silently redirect a read or a write.
"""

import os

import gspread
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

STOCKS_SHEET_ID = "1_Fj4lKv2esBxwwVn-ALfHNJp3OGNUChe8lQ5zfMkzD0"
STOCKS_WORKSHEET = "Clean_Stocks"

PROPERTY_SHEET_ID = "142q0VXqiC6RWSjcS67BGR_ROVLYFtl61QgmRrYhoUkQ"
PROPERTY_WORKSHEET = "Syndicate_Data"


@st.cache_resource
def get_credentials():
    """Local 'credentials.json' wins; Streamlit Cloud falls back to secrets."""
    if os.path.exists("credentials.json"):
        return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
    if "gcp_service_account" in st.secrets:
        return ServiceAccountCredentials.from_json_keyfile_dict(
            dict(st.secrets["gcp_service_account"]), SCOPE
        )
    st.error("🚨 Connection Failed: No credentials found.")
    st.stop()


def get_client():
    return gspread.authorize(get_credentials())


def open_worksheet(sheet_id, worksheet_name):
    return get_client().open_by_key(sheet_id).worksheet(worksheet_name)


def open_property_worksheet():
    return open_worksheet(PROPERTY_SHEET_ID, PROPERTY_WORKSHEET)


def open_stocks_worksheet():
    return open_worksheet(STOCKS_SHEET_ID, STOCKS_WORKSHEET)
