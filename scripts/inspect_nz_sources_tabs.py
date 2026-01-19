from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.sheets import get_gspread_client


TAB_NAMES = ["APP_INPUTS", "APP_SOURCES", "Sources", "SOURCES_LOG"]


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
    secrets = _load_secrets_toml()
    value = secrets.get("return_ladder_template_sheet_id")
    if value:
        return str(value).strip()
    raise RuntimeError("return_ladder_template_sheet_id missing in secrets")


def _normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _normalize_company(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(text).lower())
    tokens = [token for token in cleaned.split() if token not in {"limited", "ltd", "group", "holdings"}]
    return " ".join(tokens).strip()


def _find_header(values: list[list[str]]) -> tuple[int | None, list[str]]:
    for idx, row in enumerate(values):
        if any(str(cell).strip() for cell in row):
            return idx, [str(cell).strip() for cell in row]
    return None, []


def _row_is_empty(row: list[str]) -> bool:
    return all(str(cell).strip() == "" for cell in row)


def _coerce_headers(headers: list[str]) -> list[str]:
    coerced = []
    seen = {}
    for idx, header in enumerate(headers, start=1):
        name = str(header).strip()
        if not name:
            name = f"COL_{idx}"
        normalized = _normalize_header(name)
        if normalized in seen:
            seen[normalized] += 1
            name = f"{name}_{seen[normalized]}"
        else:
            seen[normalized] = 1
        coerced.append(name)
    return coerced


def _rows_as_dicts(headers: list[str], rows: list[list[str]], limit: int) -> list[dict[str, str]]:
    header_keys = _coerce_headers(headers)
    results: list[dict[str, str]] = []
    for row in rows:
        if _row_is_empty(row):
            continue
        values = [str(cell).strip() for cell in row]
        padded = values + [""] * max(0, len(header_keys) - len(values))
        entry = dict(zip(header_keys, padded[: len(header_keys)]))
        results.append(entry)
        if len(results) >= limit:
            break
    return results


def _detect_mode(headers: list[str]) -> str:
    norms = [_normalize_header(h) for h in headers if str(h).strip()]
    norm_set = set(norms)
    if "field" in norm_set and "value" in norm_set:
        return "LONG"
    if "field" in norm_set and "url" in norm_set:
        return "LONG"
    if any("netcash" in norm or "fcf1" in norm for norm in norms):
        return "WIDE"
    return "UNKNOWN"


def _company_name_headers(headers: list[str]) -> list[str]:
    result = []
    for header in headers:
        norm = _normalize_header(header)
        if "company" in norm or norm == "name" or norm.endswith("name"):
            result.append(header)
    return result


def _build_header_map(headers: list[str]) -> dict[str, int]:
    return {str(header): idx for idx, header in enumerate(headers)}


def _matches_ticker(row: list[str], ticker: str) -> bool:
    ticker_norm = ticker.strip().lower()
    if not ticker_norm:
        return False
    for cell in row:
        if str(cell).strip().lower() == ticker_norm:
            return True
    return False


def _matches_company_columns(row: list[str], headers: list[str], company: str) -> bool:
    if not company:
        return False
    company_norm = _normalize_company(company)
    if not company_norm:
        return False
    name_headers = _company_name_headers(headers)
    if not name_headers:
        return False
    header_map = _build_header_map(headers)
    for header in name_headers:
        idx = header_map.get(header)
        if idx is None or idx >= len(row):
            continue
        cell_norm = _normalize_company(row[idx])
        if cell_norm and cell_norm == company_norm:
            return True
    return False


def _matches_company_fallback(row: list[str], company: str) -> bool:
    if not company:
        return False
    company_norm = _normalize_company(company)
    if not company_norm:
        return False
    for cell in row:
        cell_norm = _normalize_company(cell)
        if cell_norm and company_norm in cell_norm:
            return True
    return False


def _row_matches(row: list[str], headers: list[str], ticker: str, company: str | None) -> bool:
    if _matches_ticker(row, ticker):
        return True
    if company and _matches_company_columns(row, headers, company):
        return True
    if company and _matches_company_fallback(row, company):
        return True
    return False


def _get_app_inputs_company(tab_info: dict[str, object], ticker: str) -> str | None:
    header_idx = tab_info.get("header_idx")
    headers = tab_info.get("headers") or []
    values = tab_info.get("values") or []
    if header_idx is None or not headers:
        return None
    norm_headers = {_normalize_header(h): idx for idx, h in enumerate(headers)}
    ticker_idx = norm_headers.get("ticker")
    company_idx = norm_headers.get("company")
    if ticker_idx is None or company_idx is None:
        return None
    data_rows = values[header_idx + 1 :] if header_idx + 1 < len(values) else []
    target = ticker.strip().lower()
    for row in data_rows:
        if _row_is_empty(row):
            continue
        if ticker_idx >= len(row):
            continue
        if str(row[ticker_idx]).strip().lower() == target:
            if company_idx < len(row):
                return str(row[company_idx]).strip() or None
            return None
    return None


def _print_header_info(tab_name: str, header_idx: int | None, headers: list[str], mode: str):
    if header_idx is None:
        print(f"{tab_name}: MISSING or empty")
        return
    print(f"{tab_name}: header row {header_idx + 1}")
    print(f"{tab_name}: headers={headers}")
    print(f"{tab_name}: mode={mode}")


def _print_rows(label: str, rows: list[dict[str, str]]):
    if not rows:
        print(f"{label}: no rows")
        return
    print(f"{label}:")
    for row in rows:
        print(row)


def main() -> int:
    tickers = [arg.strip() for arg in sys.argv[1:] if arg.strip()]
    try:
        sheet_id = _get_template_sheet_id()
    except Exception as exc:
        print(str(exc))
        return 1

    client = get_gspread_client()
    ss = client.open_by_key(sheet_id)

    worksheets = {ws.title: ws for ws in ss.worksheets()}
    tab_data: dict[str, dict[str, object]] = {}

    for tab_name in TAB_NAMES:
        ws = worksheets.get(tab_name)
        if not ws:
            tab_data[tab_name] = {
                "header_idx": None,
                "headers": [],
                "mode": "UNKNOWN",
                "values": [],
            }
            continue
        values = ws.get_all_values()
        header_idx, headers = _find_header(values)
        mode = _detect_mode(headers)
        tab_data[tab_name] = {
            "header_idx": header_idx,
            "headers": headers,
            "mode": mode,
            "values": values,
        }

    for tab_name in TAB_NAMES:
        info = tab_data[tab_name]
        _print_header_info(
            tab_name,
            info["header_idx"],
            info["headers"],
            info["mode"],
        )

    for tab_name in ("APP_SOURCES", "Sources"):
        info = tab_data.get(tab_name, {})
        header_idx = info.get("header_idx")
        headers = info.get("headers") or []
        values = info.get("values") or []
        if header_idx is None:
            _print_rows(f"{tab_name} sample rows", [])
            continue
        data_rows = values[header_idx + 1 :] if header_idx + 1 < len(values) else []
        sample_rows = _rows_as_dicts(headers, data_rows, limit=10)
        _print_rows(f"{tab_name} sample rows (first 10)", sample_rows)

    if tickers:
        app_inputs_info = tab_data.get("APP_INPUTS", {})
        for ticker in tickers:
            company = _get_app_inputs_company(app_inputs_info, ticker)
            print(f"Ticker: {ticker} | APP_INPUTS Company: {company or 'None'}")
            for tab_name in ("APP_SOURCES", "Sources"):
                info = tab_data.get(tab_name, {})
                header_idx = info.get("header_idx")
                headers = info.get("headers") or []
                values = info.get("values") or []
                if header_idx is None:
                    _print_rows(f"Matched tab: {tab_name}", [])
                    continue
                data_rows = values[header_idx + 1 :] if header_idx + 1 < len(values) else []
                matches = [
                    row
                    for row in data_rows
                    if not _row_is_empty(row) and _row_matches(row, headers, ticker, company)
                ]
                match_dicts = _rows_as_dicts(headers, matches, limit=len(matches))
                _print_rows(f"Matched tab: {tab_name}", match_dicts)
    else:
        print("No tickers provided; skipping ticker match lookup.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
