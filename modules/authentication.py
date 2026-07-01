import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os

@st.cache_resource(ttl=3600)
def get_client():
    """
    Authorized gspread client. Tries Streamlit Secrets first, falls back to
    a local credentials.json. Use this when you need to open sheets by
    key/url (Spreadsheet.client is a low-level HTTPClient that can't).
    """
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    if "gcp_service_account" in st.secrets:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    elif os.path.exists("credentials.json"):
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    else:
        st.error("🚨 Login Failed: Could not find 'credentials.json' OR '.streamlit/secrets.toml'.")
        st.stop()

    return gspread.authorize(creds)


@st.cache_resource(ttl=3600)
def connect_to_sheet(sheet_name="Share Portfolio"):
    """
    Smart Connector: Tries Secrets first, falls back to credentials.json
    """
    try:
        return get_client().open(sheet_name)
    except Exception as e:
        st.error(f"🔌 Connection Error: {e}")
        st.stop()
