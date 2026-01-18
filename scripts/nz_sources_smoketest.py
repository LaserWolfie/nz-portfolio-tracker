from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client
from src.services.sources_registry import find_best_source, load_sources


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
        print("Usage: python scripts/nz_sources_smoketest.py EBO NZK IFT")
        return 1

    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        print("Missing return_ladder_template_sheet_id in secrets.")
        return 1
    sources_tab = str(st.secrets.get("return_ladder_template_sources_tab", "Sources")).strip()

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    sources = load_sources(ss, sources_tab)

    for ticker in tickers:
        ticker_norm = _normalize_ticker(ticker)
        entry = find_best_source(sources, ticker_norm, None)
        if not entry:
            print(f"{ticker_norm}: MISSING source entry")
            continue
        url = entry.get("url")
        netcash = entry.get("netcash_bn")
        fcf1 = entry.get("fcf1_bn")
        print(
            f"{ticker_norm} | url={_format(url)} | "
            f"netcash_bn={_format(netcash)} | fcf1_bn={_format(fcf1)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
