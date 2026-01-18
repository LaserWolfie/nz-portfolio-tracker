from __future__ import annotations

import sys
from pathlib import Path

import gspread

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from src.data.sheets import get_gspread_client
except Exception:  # pragma: no cover - fallback path for plain python runs
    get_gspread_client = None


def _load_secrets_toml() -> dict:
    secrets_path = Path(".streamlit") / "secrets.toml"
    if not secrets_path.exists():
        return {}
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    try:
        import tomli
    except ImportError as exc:
        raise RuntimeError("tomllib/tomli required to parse secrets.toml") from exc
    return tomli.loads(secrets_path.read_text(encoding="utf-8"))


def _get_template_sheet_id() -> str:
    sheet_id = None
    try:
        import streamlit as st

        sheet_id = st.secrets.get("return_ladder_template_sheet_id")
    except Exception:
        sheet_id = None
    if sheet_id:
        return str(sheet_id).strip()
    secrets = _load_secrets_toml()
    sheet_id = secrets.get("return_ladder_template_sheet_id")
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id not found in secrets")
    return str(sheet_id).strip()


def _get_gspread_client() -> tuple[gspread.Client, str]:
    if get_gspread_client is not None:
        try:
            return get_gspread_client(), "auth=project get_gspread_client"
        except Exception:
            pass
    credentials_path = REPO_ROOT / "credentials.json"
    if credentials_path.exists():
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            str(credentials_path),
            scope,
        )
        return gspread.authorize(creds), "auth=credentials.json"
    raise RuntimeError(
        "Could not authenticate. Either ensure credentials.json exists, "
        "or add gcp_service_account to .streamlit/secrets.toml"
    )


def _ensure_tab(ws, title: str, rows: int, cols: int):
    try:
        return ws.worksheet(title)
    except Exception:
        return ws.add_worksheet(title=title, rows=rows, cols=cols)


def _ensure_header_row(worksheet, headers: list[str]):
    row1 = worksheet.row_values(1)
    if not row1:
        worksheet.update(range_name="A1", values=[headers])
        return row1, headers
    return row1, row1


def _append_missing_headers(worksheet, expected_headers: list[str]):
    row1 = worksheet.row_values(1)
    if not row1:
        worksheet.update(range_name="A1", values=[expected_headers])
        return [], expected_headers
    missing = [h for h in expected_headers if h not in row1]
    if missing:
        updated = row1 + missing
        worksheet.update(range_name="A1", values=[updated])
        return row1, updated
    return row1, row1


def main() -> int:
    sheet_id = _get_template_sheet_id()
    client, auth_used = _get_gspread_client()
    ss = client.open_by_key(sheet_id)

    expected_app_inputs_headers = [
        "Company",
        "Ticker",
        "Market",
        "CCY",
        "Price",
        "Shares_bn",
        "Net_cash_debt_bn",
        "FCF1_bn",
        "g_y1y5",
        "N_years",
        "g_terminal",
        "Notes",
        "Links",
    ]
    app_sources_headers = ["Ticker", "Field", "Value", "AsOf", "SourceURL"]

    app_inputs_ws = _ensure_tab(ss, "APP_INPUTS", rows=200, cols=len(expected_app_inputs_headers) + 5)
    app_sources_ws = _ensure_tab(ss, "APP_SOURCES", rows=200, cols=len(app_sources_headers) + 5)

    before_inputs, after_inputs = _append_missing_headers(
        app_inputs_ws,
        expected_app_inputs_headers,
    )
    before_sources, after_sources = _ensure_header_row(
        app_sources_ws,
        app_sources_headers,
    )

    print("APP_INPUTS headers before:", before_inputs)
    print("APP_INPUTS headers after: ", after_inputs)
    if not before_sources:
        print("APP_SOURCES headers before: []")
        print("APP_SOURCES headers after: ", after_sources)

    tabs = [ws.title for ws in ss.worksheets()]
    print(f"OK: '{ss.title}' updated. Tabs present: {', '.join(tabs)} ({auth_used})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
