import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os

@st.cache_resource(ttl=3600)
def connect_to_sheet(sheet_name="Share Portfolio"):
    """
    Smart Connector: Tries Secrets first, falls back to credentials.json
    """
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = None

    try:
        # OPTION 1: Try Streamlit Secrets (Best for Cloud)
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        
        # OPTION 2: Try Local File (Best for your computer)
        elif os.path.exists("credentials.json"):
            # This looks for the file in your root folder
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            
        else:
            st.error("🚨 Login Failed: Could not find 'credentials.json' OR '.streamlit/secrets.toml'.")
            st.stop()

        # Connect
        client = gspread.authorize(creds)
        return client.open(sheet_name)

    except Exception as e:
        st.error(f"🔌 Connection Error: {e}")
        st.stop()