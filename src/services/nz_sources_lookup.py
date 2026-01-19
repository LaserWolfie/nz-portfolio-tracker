from __future__ import annotations

import re
from typing import Iterable

from src.data.sheets import get_gspread_client


_ALIAS_MAP = {
    "EBO": ["EBOS"],
    "EBOS": ["EBO"],
}


def _normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _normalize_ticker(value: str) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
    return text


def _split_ticker_shares(value: str) -> tuple[str, bool]:
    text = _normalize_ticker(value)
    if not text:
        return "", False
    if re.search(r"\s+shares?$", text, flags=re.IGNORECASE):
        base = re.sub(r"\s+shares?$", "", text, flags=re.IGNORECASE).strip()
        return base, True
    return text, False


def _parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]
    cleaned = text.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if is_negative else number


def _find_header_row(values: list[list[str]]) -> tuple[int | None, list[str]]:
    for idx, row in enumerate(values):
        if any(str(cell).strip() for cell in row):
            return idx, [str(cell).strip() for cell in row]
    return None, []


def _header_index(headers: list[str], keys: Iterable[str]) -> int | None:
    normalized = {_normalize_header(header): idx for idx, header in enumerate(headers)}
    for key in keys:
        idx = normalized.get(_normalize_header(key))
        if idx is not None:
            return idx
    return None


def load_sources_entries(spreadsheet, tab_name: str = "Sources") -> list[dict]:
    try:
        ws = spreadsheet.worksheet(tab_name)
    except Exception:
        return []
    values = ws.get_all_values()
    header_idx, headers = _find_header_row(values)
    if header_idx is None:
        return []

    ticker_idx = _header_index(headers, ["Ticker", "TICKER"])
    company_idx = _header_index(headers, ["Company", "COMPANY"])
    netcash_idx = _header_index(
        headers,
        ["NetCash_bn", "Net cash/(debt) (bn)", "NetCash", "NetCashDebt_bn"],
    )
    fcf1_idx = _header_index(headers, ["FCF1_bn", "FCF1", "FCF1 (next-year, bn)"])
    url_idx = _header_index(headers, ["URL", "Link", "SourceURL"])
    if ticker_idx is None:
        return []

    entries: list[dict] = []
    for row in values[header_idx + 1 :]:
        if not any(str(cell).strip() for cell in row):
            continue
        ticker_raw = str(row[ticker_idx]).strip() if ticker_idx < len(row) else ""
        ticker_base, is_shares = _split_ticker_shares(ticker_raw)
        if not ticker_base:
            continue
        company = str(row[company_idx]).strip() if company_idx is not None and company_idx < len(row) else ""
        netcash = _parse_float(row[netcash_idx]) if netcash_idx is not None and netcash_idx < len(row) else None
        fcf1 = _parse_float(row[fcf1_idx]) if fcf1_idx is not None and fcf1_idx < len(row) else None
        url = str(row[url_idx]).strip() if url_idx is not None and url_idx < len(row) else ""
        entries.append(
            {
                "ticker_raw": ticker_raw,
                "ticker_base": ticker_base,
                "is_shares": is_shares,
                "company": company,
                "netcash_bn": netcash,
                "fcf1_bn": fcf1,
                "url": url,
            }
        )
    return entries


def _alias_candidates(ticker: str) -> list[str]:
    base = _normalize_ticker(ticker)
    aliases = _ALIAS_MAP.get(base, [])
    return [base] + aliases


def _collect_group(entries: list[dict], ticker: str, include_shares: bool) -> list[dict]:
    result = []
    for entry in entries:
        if entry["ticker_base"] != ticker:
            continue
        if entry["is_shares"] and not include_shares:
            continue
        if not entry["is_shares"] and include_shares:
            continue
        result.append(entry)
    return result


def lookup_sources(entries: list[dict], ticker: str) -> dict:
    candidates = _alias_candidates(ticker)
    clean_group = []
    matched_key = None
    for candidate in candidates:
        group = _collect_group(entries, candidate, include_shares=False)
        if group:
            clean_group = group
            matched_key = candidate
            break

    shares_group = []
    shares_key = None
    if not clean_group:
        for candidate in candidates:
            group = _collect_group(entries, candidate, include_shares=True)
            if group:
                shares_group = group
                shares_key = candidate
                break

    selected = clean_group if clean_group else shares_group
    matched_key = matched_key or shares_key
    netcash_bn = None
    fcf1_bn = None
    urls: list[str] = []
    for entry in selected:
        if netcash_bn is None and entry.get("netcash_bn") is not None:
            netcash_bn = entry.get("netcash_bn")
        if fcf1_bn is None and entry.get("fcf1_bn") is not None:
            fcf1_bn = entry.get("fcf1_bn")
        url = entry.get("url")
        if url and url not in urls:
            urls.append(url)

    return {
        "matched_key": matched_key,
        "netcash_bn": netcash_bn,
        "fcf1_bn": fcf1_bn,
        "urls": urls,
        "used_shares_rows": bool(shares_group) and not clean_group,
    }


def load_sources_wide(sheet_id: str, tab_name: str = "Sources") -> dict[str, dict[str, object]]:
    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)
    try:
        ws = ss.worksheet(tab_name)
    except Exception:
        return {}
    values = ws.get_all_values()
    header_idx, headers = _find_header_row(values)
    if header_idx is None:
        return {}

    ticker_idx = _header_index(headers, ["Ticker", "TICKER"])
    company_idx = _header_index(headers, ["Company", "COMPANY"])
    netcash_idx = _header_index(headers, ["NetCash_bn", "Net cash/(debt) (bn)", "NetCash"])
    fcf1_idx = _header_index(headers, ["FCF1_bn", "FCF1", "FCF1 (next-year, bn)"])
    url_idx = _header_index(headers, ["URL", "Link", "SourceURL"])
    if ticker_idx is None:
        return {}

    entries: dict[str, dict[str, object]] = {}
    for row in values[header_idx + 1 :]:
        if not any(str(cell).strip() for cell in row):
            continue
        ticker_raw = str(row[ticker_idx]).strip() if ticker_idx < len(row) else ""
        if not ticker_raw:
            continue
        ticker = _normalize_ticker(ticker_raw)
        company = str(row[company_idx]).strip() if company_idx is not None and company_idx < len(row) else ""
        netcash = _parse_float(row[netcash_idx]) if netcash_idx is not None and netcash_idx < len(row) else None
        fcf1 = _parse_float(row[fcf1_idx]) if fcf1_idx is not None and fcf1_idx < len(row) else None
        url = str(row[url_idx]).strip() if url_idx is not None and url_idx < len(row) else ""

        entry = entries.get(ticker, {"company": "", "netcash_bn": None, "fcf1_bn": None, "url": ""})
        if company and not entry.get("company"):
            entry["company"] = company
        if netcash is not None and entry.get("netcash_bn") is None:
            entry["netcash_bn"] = netcash
        if fcf1 is not None and entry.get("fcf1_bn") is None:
            entry["fcf1_bn"] = fcf1
        if url and not entry.get("url"):
            entry["url"] = url
        entries[ticker] = entry

    for base, aliases in _ALIAS_MAP.items():
        if base not in entries:
            for alias in aliases:
                if alias in entries:
                    entries[base] = entries[alias]
                    break
        else:
            for alias in aliases:
                if alias in entries:
                    entry = entries[base]
                    alias_entry = entries[alias]
                    if not entry.get("company") and alias_entry.get("company"):
                        entry["company"] = alias_entry.get("company")
                    if entry.get("netcash_bn") is None and alias_entry.get("netcash_bn") is not None:
                        entry["netcash_bn"] = alias_entry.get("netcash_bn")
                    if entry.get("fcf1_bn") is None and alias_entry.get("fcf1_bn") is not None:
                        entry["fcf1_bn"] = alias_entry.get("fcf1_bn")
                    if not entry.get("url") and alias_entry.get("url"):
                        entry["url"] = alias_entry.get("url")
                    entries[base] = entry
                    break

    return entries
