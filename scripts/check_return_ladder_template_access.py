# README: Add `return_ladder_template_sheet_id = "<SHEET_ID>"` to `.streamlit/secrets.toml`.
import os
import sys

import gspread
import streamlit as st
from oauth2client.service_account import ServiceAccountCredentials


def _get_template_sheet_id() -> str | None:
    keys = [
        "return_ladder_template_sheet_id",
        "RETURN_LADDER_TEMPLATE_SHEET_ID",
    ]
    for key in keys:
        value = st.secrets.get(key)
        if value:
            return str(value).strip()
    google_sheets = st.secrets.get("google_sheets") or {}
    if isinstance(google_sheets, dict):
        value = google_sheets.get("return_ladder_template_sheet_id")
        if value:
            return str(value).strip()
    return None


def _get_client():
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
        raise RuntimeError("No credentials found (credentials.json or st.secrets['gcp_service_account']).")
    return gspread.authorize(creds)


def _read_preview(ws, title: str, limit: int = 10):
    values = ws.get_all_values()
    if not values:
        print(f"{title}: empty")
        return
    rows = values[:limit]
    print(f"{title} first {min(limit, len(rows))} rows:")
    for row in rows:
        print(row)


def main() -> int:
    sheet_id = _get_template_sheet_id()
    if not sheet_id:
        print("Missing return_ladder_template_sheet_id in secrets.")
        return 1

    try:
        client = _get_client()
        ss = client.open_by_key(sheet_id)
        worksheets = ss.worksheets()
        print("Template sheet title:", ss.title)
        print("Tabs:", [ws.title for ws in worksheets])
    except Exception as exc:
        print(f"Failed to open template sheet: {exc}")
        return 1

    try:
        model_tab = str(st.secrets.get("return_ladder_template_model_tab", "Model")).strip()
        sources_tab = str(st.secrets.get("return_ladder_template_sources_tab", "Sources")).strip()
        model_ws = next((ws for ws in worksheets if ws.title == model_tab), None)
        sources_ws = next((ws for ws in worksheets if ws.title == sources_tab), None)
        if model_ws:
            _read_preview(model_ws, model_tab)
        if sources_ws:
            _read_preview(sources_ws, sources_tab)
        if not model_ws and not sources_ws and worksheets:
            _read_preview(worksheets[0], worksheets[0].title)
    except Exception as exc:
        print(f"Failed to read template sheet: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
