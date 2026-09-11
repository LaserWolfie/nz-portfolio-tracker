"""Small Streamlit helpers shared between pages.

Kept separate from `extraction` and `deltas`, which are deliberately free of
Streamlit imports so they can be tested and reused headlessly.
"""

import os

import streamlit as st

from modules import deltas, sheets, storage

#: Severity to a glyph, so the same flag looks the same on every page.
SEVERITY_ICON = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "⚪",
}


def require_portfolio_data():
    """The session_state guard every page opens with.

    Pages cannot be loaded directly -- Home must run first to populate
    session_state -- so stop with an explanation rather than a KeyError.
    """
    if 'prop_df' not in st.session_state or st.session_state.prop_df.empty:
        st.warning("⚠️ Data missing. Please go to the **Home** page first.")
        st.stop()
    return st.session_state.prop_df.copy()


def anthropic_credentials(show_status: bool = True):
    """Resolve an API key, or confirm the SDK can find credentials itself.

    Returns (api_key_or_None, have_credentials). A None key with
    have_credentials True means the SDK will resolve it from the environment,
    an `ant auth login` profile, or Workload Identity Federation.
    """
    if "ANTHROPIC_API_KEY" in st.secrets:
        if show_status:
            st.caption("🔑 API key loaded from secrets")
        return st.secrets["ANTHROPIC_API_KEY"], True

    if os.environ.get("ANTHROPIC_API_KEY"):
        if show_status:
            st.caption("🔑 Using credentials from the environment")
        return None, True

    key = st.text_input("Anthropic API key", type="password") or None
    if not key and show_status:
        st.info(
            "No credentials found. Enter a key above, or set ANTHROPIC_API_KEY in "
            "`.streamlit/secrets.toml` or the environment."
        )
    return key, bool(key)


@st.cache_resource(ttl=300)
def _property_spreadsheet():
    return sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)


def load_pipeline_data():
    """Read the two storage tabs. Returns (spreadsheet, periods, baseline).

    On failure the error is shown and empty lists returned, so a page renders
    something useful rather than a traceback.
    """
    try:
        spreadsheet = _property_spreadsheet()
        periods = spreadsheet.worksheet(storage.PERIODS_WORKSHEET).get_all_records()
        baseline = storage.load_baseline(
            spreadsheet.worksheet(storage.BASELINE_WORKSHEET))
        return spreadsheet, periods, baseline
    except Exception as e:  # noqa: BLE001 - a page must still render
        st.error(f"Could not read the storage tabs: {e}")
        return None, [], []


def render_flag(flag):
    """One flag, coloured by how much it matters."""
    icon = SEVERITY_ICON.get(str(flag.severity), "")
    line = f"{icon} **{flag.severity}** — {flag.message}"
    if flag.severity >= deltas.Severity.HIGH:
        st.error(line)
    elif flag.severity == deltas.Severity.MEDIUM:
        st.warning(line)
    else:
        st.info(line)
