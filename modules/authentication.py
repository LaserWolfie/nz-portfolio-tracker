import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os

@st.cache_resource(ttl=3600)
def connect_to_sheet(sheet_name="Share Portfolio"):
    """
    Connects to Google Sheets.
    PRIORITY: Checks for local 'credentials.json' FIRST.
    """
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = None

    # ---------------------------------------------------------
    # PRIORITY 1: LOCAL MODE (Your Computer)
    # ---------------------------------------------------------
    if os.path.exists("credentials.json"):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        except Exception as e:
            st.error(f"❌ Found credentials.json but couldn't read it: {e}")
            st.stop()

    # ---------------------------------------------------------
    # PRIORITY 2: CLOUD MODE (Streamlit Server)
    # ---------------------------------------------------------
    # We only look for secrets if the local file is MISSING
    else:
        try:
            if "gcp_service_account" in st.secrets:
                creds_dict = dict(st.secrets["gcp_service_account"])
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            else:
                st.error("🚨 Login Failed: No 'credentials.json' found AND no Secrets configured.")
                st.stop()
        except Exception as e:
            st.error(f"❌ Cloud Secrets Error: {e}")
            st.stop()

    # ---------------------------------------------------------
    # CONNECT
    # ---------------------------------------------------------
    try:
        client = gspread.authorize(creds)
        return client.open(sheet_name)
    except Exception as e:
        st.error(f"Connection Error: {e}")
        st.stop()
