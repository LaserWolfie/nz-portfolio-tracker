from __future__ import annotations

from pathlib import Path
import re
import sys

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client


APP_INPUTS_TAB = "APP_INPUTS"

REQUIRED_HEADERS = [
    "Company",
    "Ticker",
    "Market",
    "CCY",
    "Price",
    "Shares (bn)",
    "Net cash/(debt) (bn)",
    "FCF1 (next-year, bn)",
    "g (Y1-Y5)",
    "N (yrs)",
    "g terminal",
]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _find_header_map(headers: list[str]) -> dict[str, int]:
    normalized = {_normalize(header): idx for idx, header in enumerate(headers)}
    mapping = {}
    for name in REQUIRED_HEADERS:
        idx = normalized.get(_normalize(name))
        if idx is not None:
            mapping[name] = idx
    return mapping


def _is_blank(value) -> bool:
    return str(value or "").strip() == ""


def main() -> int:
    sheet_id = str(st.secrets.get("return_ladder_template_sheet_id", "")).strip()
    if not sheet_id:
        raise RuntimeError("return_ladder_template_sheet_id missing in secrets")

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    ws = ss.worksheet(APP_INPUTS_TAB)
    values = ws.get_all_values()
    if not values:
        print("APP_INPUTS is empty.")
        return 1

    headers = values[0]
    header_map = _find_header_map(headers)
    print("Detected headers:")
    for key, idx in header_map.items():
        print(f"  {key}: column {idx + 1} ({headers[idx]})")
    print(f"Row count (including header): {len(values)}")

    ticker_idx = header_map.get("Ticker")
    if ticker_idx is None:
        print("Ticker column not found; cannot report per-ticker missing fields.")
        return 1

    print("\nPer-ticker missing fields:")
    for row in values[1:]:
        ticker = row[ticker_idx] if ticker_idx < len(row) else ""
        ticker = str(ticker).strip().upper()
        if not ticker:
            continue
        missing = []
        for name, idx in header_map.items():
            if idx >= len(row) or _is_blank(row[idx]):
                missing.append(name)
        if missing:
            print(f"- {ticker}: missing {', '.join(missing)}")

    print("\nRun:")
    print("  python scripts/diagnose_return_ladder_inputs.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
