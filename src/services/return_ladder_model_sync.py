from __future__ import annotations

import re

from src.services.return_ladder_app_inputs import build_header_map


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _normalize_ticker(value: str) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
    return text


def _parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    cleaned = text.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _append_links(existing: str, url: str | None) -> str:
    existing_text = str(existing or "").strip()
    if not url:
        return existing_text
    if existing_text and url in existing_text:
        return existing_text
    if existing_text:
        return f"{existing_text} | {url}"
    return url


def _find_header_row(values: list[list[str]]) -> tuple[int | None, list[str]]:
    for idx, row in enumerate(values):
        if any(str(cell).strip() for cell in row):
            return idx, [str(cell).strip() for cell in row]
    return None, []


def _header_contains(header: str, token: str) -> bool:
    return token in _normalize(header)


def read_model_inputs(sheet, model_tab: str = "Model") -> dict[str, dict[str, float | None]]:
    try:
        ws = sheet.worksheet(model_tab)
    except Exception:
        return {}

    values = ws.get_all_values()
    if not values:
        return {}

    header_idx = None
    header_row: list[str] = []
    for idx, row in enumerate(values[:50]):
        normalized = [_normalize(cell) for cell in row]
        if not normalized:
            continue
        has_ticker = any("ticker" in cell for cell in normalized)
        has_netcash = any("netcash" in cell or "netcashdebt" in cell for cell in normalized)
        has_fcf1 = any("fcf1" in cell for cell in normalized)
        if has_ticker and has_netcash and has_fcf1:
            header_idx = idx
            header_row = [str(cell).strip() for cell in row]
            break

    if header_idx is None:
        return {}

    ticker_idx = next((i for i, h in enumerate(header_row) if _header_contains(h, "ticker")), None)
    netcash_idx = next((i for i, h in enumerate(header_row) if _header_contains(h, "netcash")), None)
    fcf1_idx = next((i for i, h in enumerate(header_row) if _header_contains(h, "fcf1")), None)
    if ticker_idx is None:
        return {}

    results: dict[str, dict[str, float | None]] = {}
    for row in values[header_idx + 1 :]:
        if ticker_idx >= len(row):
            continue
        ticker_raw = str(row[ticker_idx]).strip()
        if not ticker_raw:
            continue
        ticker = _normalize_ticker(ticker_raw)
        netcash_val = _parse_float(row[netcash_idx]) if netcash_idx is not None and netcash_idx < len(row) else None
        fcf1_val = _parse_float(row[fcf1_idx]) if fcf1_idx is not None and fcf1_idx < len(row) else None
        results[ticker] = {"netcash_bn": netcash_val, "fcf1_bn": fcf1_val}

    return results


def _find_sources_url(sheet, ticker: str, sources_tab: str, app_sources_tab: str) -> str | None:
    target = _normalize_ticker(ticker)

    try:
        ws = sheet.worksheet(sources_tab)
    except Exception:
        ws = None
    if ws:
        values = ws.get_all_values()
        header_idx, headers = _find_header_row(values)
        if header_idx is not None:
            ticker_idx = next((i for i, h in enumerate(headers) if _header_contains(h, "ticker")), None)
            url_idx = next((i for i, h in enumerate(headers) if _header_contains(h, "url")), None)
            if ticker_idx is not None and url_idx is not None:
                for row in values[header_idx + 1 :]:
                    if ticker_idx >= len(row):
                        continue
                    raw = str(row[ticker_idx]).strip()
                    if not raw:
                        continue
                    if re.search(r"\bshares\b", raw, flags=re.IGNORECASE):
                        continue
                    if _normalize_ticker(raw) == target:
                        if url_idx < len(row):
                            url = str(row[url_idx]).strip()
                            if url:
                                return url

    try:
        ws = sheet.worksheet(app_sources_tab)
    except Exception:
        ws = None
    if ws:
        values = ws.get_all_values()
        header_idx, headers = _find_header_row(values)
        if header_idx is not None:
            ticker_idx = next((i for i, h in enumerate(headers) if _header_contains(h, "ticker")), None)
            field_idx = next((i for i, h in enumerate(headers) if _header_contains(h, "field")), None)
            url_idx = next((i for i, h in enumerate(headers) if _header_contains(h, "url")), None)
            if ticker_idx is not None and field_idx is not None and url_idx is not None:
                for row in values[header_idx + 1 :]:
                    if ticker_idx >= len(row) or field_idx >= len(row):
                        continue
                    raw = str(row[ticker_idx]).strip()
                    field = str(row[field_idx]).strip().lower()
                    if not raw or field != "nzx instrument":
                        continue
                    if _normalize_ticker(raw) == target and url_idx < len(row):
                        url = str(row[url_idx]).strip()
                        if url:
                            return url

    return None


def sync_nz_netcash_fcf_from_model(
    sheet,
    inputs_ws=None,
    model_tab: str = "Model",
    sources_tab: str = "Sources",
    app_sources_tab: str = "APP_SOURCES",
) -> dict:
    if inputs_ws is None:
        inputs_ws = sheet.worksheet("APP_INPUTS")
    values = inputs_ws.get_all_values()
    if not values:
        return {
            "results": [{"status": "error", "ticker": "", "message": "APP_INPUTS is empty."}],
            "tickers_updated": [],
            "fields_updated": {"net_cash": 0, "fcf1": 0, "links": 0},
            "tickers_missing_model": [],
        }

    headers = values[0]
    header_map = build_header_map(headers)
    ticker_idx = header_map.get("Ticker")
    market_idx = header_map.get("Market")
    net_cash_idx = header_map.get("Net cash/(debt) (bn)")
    fcf1_idx = header_map.get("FCF1 (next-year, bn)")
    links_idx = header_map.get("Links")
    if ticker_idx is None or market_idx is None or net_cash_idx is None or fcf1_idx is None:
        return {
            "results": [{"status": "error", "ticker": "", "message": "APP_INPUTS missing required columns."}],
            "tickers_updated": [],
            "fields_updated": {"net_cash": 0, "fcf1": 0, "links": 0},
            "tickers_missing_model": [],
        }

    model_data = read_model_inputs(sheet, model_tab=model_tab)
    updates: list[tuple[int, int, str | float]] = []
    results: list[dict] = []
    tickers_updated: list[str] = []
    tickers_missing_model: list[str] = []
    fields_updated = {"net_cash": 0, "fcf1": 0, "links": 0}

    for row_idx, row in enumerate(values[1:], start=2):
        if ticker_idx >= len(row):
            continue
        ticker = str(row[ticker_idx]).strip()
        if not ticker:
            continue
        market = str(row[market_idx]).strip().upper() if market_idx < len(row) else ""
        if market != "NZ":
            continue

        ticker_norm = _normalize_ticker(ticker)
        model_entry = model_data.get(ticker_norm)
        if not model_entry:
            tickers_missing_model.append(ticker_norm)
            results.append({"status": "ok", "ticker": ticker, "message": "missing model"})
            continue

        updated_fields: list[str] = []
        if net_cash_idx < len(row) and str(row[net_cash_idx]).strip() == "":
            netcash_bn = model_entry.get("netcash_bn")
            if netcash_bn is not None:
                updates.append((row_idx, net_cash_idx + 1, netcash_bn))
                fields_updated["net_cash"] += 1
                updated_fields.append("net_cash")
        if fcf1_idx < len(row) and str(row[fcf1_idx]).strip() == "":
            fcf1_bn = model_entry.get("fcf1_bn")
            if fcf1_bn is not None:
                updates.append((row_idx, fcf1_idx + 1, fcf1_bn))
                fields_updated["fcf1"] += 1
                updated_fields.append("fcf1")

        if updated_fields and links_idx is not None:
            link_value = str(row[links_idx]).strip() if links_idx < len(row) else ""
            source_url = _find_sources_url(sheet, ticker_norm, sources_tab, app_sources_tab)
            updated_links = _append_links(link_value, source_url)
            if updated_links != link_value:
                updates.append((row_idx, links_idx + 1, updated_links))
                fields_updated["links"] += 1
                updated_fields.append("links")

        if updated_fields:
            tickers_updated.append(ticker_norm)
            results.append({"status": "ok", "ticker": ticker, "message": "updated", "updated_fields": updated_fields})
        else:
            results.append({"status": "ok", "ticker": ticker, "message": "skipped"})

    if updates:
        cells = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cells, updates):
            cell.value = value
        inputs_ws.update_cells(cells, value_input_option="USER_ENTERED")

    return {
        "results": results,
        "tickers_updated": tickers_updated,
        "fields_updated": fields_updated,
        "tickers_missing_model": sorted(set(tickers_missing_model)),
    }
