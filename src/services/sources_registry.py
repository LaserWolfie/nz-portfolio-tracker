from __future__ import annotations

import re
from typing import Any


def _normalize_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").strip().upper())


def _parse_float(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def load_sources(sheet_client, tab_name: str = "Sources") -> dict[str, dict[str, Any]]:
    try:
        ws = sheet_client.worksheet(tab_name)
    except Exception:
        return {}

    values = ws.get_all_values()
    if not values:
        return {}

    headers = values[0]
    header_map = {_normalize_key(h): idx for idx, h in enumerate(headers)}

    def _col(name: str) -> int | None:
        return header_map.get(_normalize_key(name))

    ticker_col = _col("Ticker")
    company_col = _col("Company")
    url_col = _col("URL")

    def _col_any(names: list[str]) -> int | None:
        for name in names:
            idx = _col(name)
            if idx is not None:
                return idx
        return None

    netcash_col = _col_any(
        [
            "NetCash_bn",
            "Net cash/(debt) (bn)",
            "Net Cash / Debt",
            "Net cash / debt",
            "Net cash/(debt)",
        ]
    )
    fcf_col = _col_any(
        [
            "FCF1_bn",
            "FCF1 (next-year, bn)",
            "FCF1",
        ]
    )

    entries: dict[str, dict[str, Any]] = {}
    for row in values[1:]:
        key_val = ""
        if ticker_col is not None and ticker_col < len(row):
            key_val = str(row[ticker_col]).strip()
        if not key_val and company_col is not None and company_col < len(row):
            key_val = str(row[company_col]).strip()
        if not key_val:
            continue
        key_norm = _normalize_key(key_val)
        url = str(row[url_col]).strip() if url_col is not None and url_col < len(row) else ""
        netcash = _parse_float(row[netcash_col]) if netcash_col is not None and netcash_col < len(row) else None
        fcf1 = _parse_float(row[fcf_col]) if fcf_col is not None and fcf_col < len(row) else None
        entries[key_norm] = {
            "key": key_val,
            "key_norm": key_norm,
            "url": url or None,
            "netcash_bn": netcash,
            "fcf1_bn": fcf1,
        }
    return entries


def find_best_source(sources: dict[str, dict[str, Any]], ticker_norm: str, company_name: str | None = None):
    if not sources:
        return None
    ticker_key = _normalize_key(ticker_norm)
    if ticker_key in sources:
        return sources[ticker_key]
    for entry in sources.values():
        if entry["key_norm"].startswith(ticker_key):
            return entry
    for entry in sources.values():
        if ticker_key in entry["key_norm"]:
            return entry
    if company_name:
        tokens = [_normalize_key(tok) for tok in str(company_name).split() if tok.strip()]
        for entry in sources.values():
            for token in tokens:
                if token and token in entry["key_norm"]:
                    return entry
    return None
