from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client
from src.services.return_ladder_app_inputs import ensure_app_inputs_schema, seed_app_inputs_formulas

APP_INPUTS_TAB = "APP_INPUTS"


def main() -> int:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    inputs_ws = ss.worksheet(APP_INPUTS_TAB)

    ensure_app_inputs_schema(inputs_ws)
    seed_app_inputs_formulas(inputs_ws)
    print("Seeded APP_INPUTS schema + formulas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
