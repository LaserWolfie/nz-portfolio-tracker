from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client
from src.services.return_ladder_model_sync import read_model_inputs


def _format(value):
    if value is None:
        return "None"
    return str(value)


def _normalize_ticker(value: str) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
    return text


def main() -> int:
    tickers = [arg.strip().upper() for arg in sys.argv[1:] if arg.strip()]
    if not tickers:
        print("Usage: python scripts/nz_model_smoketest.py EBO SAN IFT")
        return 1

    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        print("Missing return_ladder_template_sheet_id in secrets.")
        return 1
    model_tab = str(st.secrets.get("return_ladder_template_model_tab", "Model")).strip()

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    data = read_model_inputs(ss, model_tab=model_tab)

    for ticker in tickers:
        entry = data.get(_normalize_ticker(ticker))
        if not entry:
            print(f"{ticker} | netcash_bn=None | fcf1_bn=None")
            continue
        print(
            f"{ticker} | netcash_bn={_format(entry.get('netcash_bn'))} | "
            f"fcf1_bn={_format(entry.get('fcf1_bn'))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
