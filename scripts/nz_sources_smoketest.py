from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.nz_sources_lookup import load_sources_wide


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
    sources_map = load_sources_wide(sheet_id)

    for ticker in tickers:
        key = _normalize_ticker(ticker)
        entry = sources_map.get(key)
        resolved_key = key
        if not entry:
            alias_key = "EBOS" if key == "EBO" else "EBO" if key == "EBOS" else None
            if alias_key:
                entry = sources_map.get(alias_key)
                resolved_key = alias_key if entry else "None"
        if not entry:
            resolved_key = "None"
        url_text = _format(entry.get("url")) if entry else "None"
        netcash = entry.get("netcash_bn") if entry else None
        fcf1 = entry.get("fcf1_bn") if entry else None
        print(
            f"{ticker} | resolved_key={resolved_key} | "
            f"netcash_bn={_format(netcash)} | "
            f"fcf1_bn={_format(fcf1)} | "
            f"url={url_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
