from __future__ import annotations

import re
from datetime import datetime, timezone

from gspread.utils import rowcol_to_a1

from src.services.nz_sources_lookup import load_sources_entries, lookup_sources
from src.services.nzx_instruments import get_nzx_snapshot

APP_INPUTS_HEADERS = [
    "Company",
    "Ticker",
    "Market",
    "CCY",
    "Price",
    "Shares_bn",
    "Net cash/(debt) (bn)",
    "FCF1 (next-year, bn)",
    "g (Y1-Y5)",
    "N (yrs)",
    "g_terminal",
    "Notes",
    "Links",
]

_HEADER_ALIASES = {
    "company": "Company",
    "ticker": "Ticker",
    "symbol": "Ticker",
    "market": "Market",
    "ccy": "CCY",
    "currency": "CCY",
    "price": "Price",
    "sharesbn": "Shares_bn",
    "shares(bn)": "Shares_bn",
    "shares_bn": "Shares_bn",
    "sharesbn.": "Shares_bn",
    "shares": "Shares_bn",
    "netcashdebtbn": "Net cash/(debt) (bn)",
    "netcash(debt)bn": "Net cash/(debt) (bn)",
    "netcash(debt)(bn)": "Net cash/(debt) (bn)",
    "netcash/(debt)bn": "Net cash/(debt) (bn)",
    "netcash/(debt)(bn)": "Net cash/(debt) (bn)",
    "netcashbn": "Net cash/(debt) (bn)",
    "net_cash_debt_bn": "Net cash/(debt) (bn)",
    "netcashdebt": "Net cash/(debt) (bn)",
    "fcf1(next-year,bn)": "FCF1 (next-year, bn)",
    "fcf1nextyearbn": "FCF1 (next-year, bn)",
    "fcf1bn": "FCF1 (next-year, bn)",
    "fcf1": "FCF1 (next-year, bn)",
    "gy1y5": "g (Y1-Y5)",
    "g(y1-y5)": "g (Y1-Y5)",
    "g1-5": "g (Y1-Y5)",
    "g15": "g (Y1-Y5)",
    "g_1_5": "g (Y1-Y5)",
    "nyrs": "N (yrs)",
    "nyears": "N (yrs)",
    "n(years)": "N (yrs)",
    "n(yrs)": "N (yrs)",
    "n": "N (yrs)",
    "gterminal": "g_terminal",
    "g_terminal": "g_terminal",
    "notes": "Notes",
    "note": "Notes",
    "links": "Links",
    "link": "Links",
    "url": "Links",
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


def _canonical_header(header: str) -> str | None:
    normalized = _normalize(header)
    if normalized in _HEADER_ALIASES:
        return _HEADER_ALIASES[normalized]
    return None


def build_header_map(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(headers):
        canonical = _canonical_header(header)
        if canonical and canonical not in mapping:
            mapping[canonical] = idx
    return mapping


def ensure_app_inputs_schema(ws) -> list[str]:
    headers = ws.row_values(1)
    if not headers:
        ws.update(range_name="A1", values=[APP_INPUTS_HEADERS])
        ws.freeze(rows=1)
        return list(APP_INPUTS_HEADERS)

    new_headers = []
    seen = set()
    dup_counters: dict[str, int] = {}
    for header in headers:
        canonical = _canonical_header(header) or str(header).strip()
        normalized = _normalize(canonical)
        if normalized in seen:
            dup_counters.setdefault(normalized, 1)
            dup_counters[normalized] += 1
            canonical = f"{canonical} (legacy {dup_counters[normalized]})"
            normalized = _normalize(canonical)
        new_headers.append(canonical)
        seen.add(normalized)

    normalized_present = {_normalize(header): True for header in new_headers if str(header).strip()}
    missing = [name for name in APP_INPUTS_HEADERS if _normalize(name) not in normalized_present]
    if missing:
        new_headers.extend(missing)

    if len(new_headers) > ws.col_count:
        ws.add_cols(len(new_headers) - ws.col_count)

    if new_headers != headers:
        ws.update(range_name="A1", values=[new_headers])
        ws.freeze(rows=1)

    return new_headers


def _col_letter(col_idx: int) -> str:
    return re.sub(r"\d", "", rowcol_to_a1(1, col_idx))


def _get_cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _is_blank(row: list[str], idx: int | None) -> bool:
    return _get_cell(row, idx) == ""


def _infer_market(ticker: str, ccy: str) -> str:
    upper = ticker.upper()
    if upper.startswith("NZ:") or upper.startswith("NZX:") or ccy.upper() == "NZD":
        return "NZ"
    return "US"


def _ticker_for_link(ticker: str) -> str:
    upper = ticker.strip()
    if upper.upper().startswith("NZX:"):
        return upper.split("NZX:", 1)[1]
    if upper.upper().startswith("NZ:"):
        return upper.split("NZ:", 1)[1]
    return upper


def seed_app_inputs_formulas(ws, market_overrides: dict[str, str] | None = None) -> int:
    headers = ws.row_values(1)
    if not headers:
        raise RuntimeError("APP_INPUTS header row is missing.")

    header_map = build_header_map(headers)
    ticker_idx = header_map.get("Ticker")
    if ticker_idx is None:
        raise RuntimeError("APP_INPUTS is missing the Ticker column.")

    values = ws.get_all_values(value_render_option="FORMULA")
    if len(values) <= 1:
        return 0

    updates = []
    for row_idx, row in enumerate(values[1:], start=2):
        ticker = _get_cell(row, ticker_idx)
        if not ticker:
            break

        market_idx = header_map.get("Market")
        ccy_idx = header_map.get("CCY")
        company_idx = header_map.get("Company")
        price_idx = header_map.get("Price")
        shares_idx = header_map.get("Shares_bn")
        net_cash_idx = header_map.get("Net cash/(debt) (bn)")
        g_idx = header_map.get("g (Y1-Y5)")
        n_idx = header_map.get("N (yrs)")
        g_terminal_idx = header_map.get("g_terminal")
        links_idx = header_map.get("Links")

        market = _get_cell(row, market_idx)
        ccy = _get_cell(row, ccy_idx)
        override_market = None
        if market_overrides:
            override_market = market_overrides.get(ticker.upper())
        market_locked = bool(market)
        inferred_market = override_market or market or _infer_market(ticker, ccy)

        if market_idx is not None and (not market_locked or override_market):
            updates.append((row_idx, market_idx + 1, inferred_market))

        if ccy_idx is not None and _is_blank(row, ccy_idx):
            if inferred_market == "US":
                updates.append((row_idx, ccy_idx + 1, "USD"))
            elif inferred_market == "NZ":
                updates.append((row_idx, ccy_idx + 1, "NZD"))

        ticker_ref = f"${_col_letter(ticker_idx + 1)}{row_idx}"
        if company_idx is not None and _is_blank(row, company_idx):
            if inferred_market == "US":
                formula = (
                    f'=IFERROR(GOOGLEFINANCE("NASDAQ:"&{ticker_ref},"name"),'
                    f'IFERROR(GOOGLEFINANCE("NYSE:"&{ticker_ref},"name"),""))'
                )
                updates.append((row_idx, company_idx + 1, formula))
            elif inferred_market == "NZ":
                updates.append((row_idx, company_idx + 1, ticker))

        if price_idx is not None and _is_blank(row, price_idx):
            if inferred_market == "US":
                formula = (
                    f'=IFERROR(GOOGLEFINANCE("NASDAQ:"&{ticker_ref},"price"),'
                    f'IFERROR(GOOGLEFINANCE("NYSE:"&{ticker_ref},"price"),'
                    f'IFERROR(GOOGLEFINANCE("AMEX:"&{ticker_ref},"price"),"")))'
                )
                updates.append((row_idx, price_idx + 1, formula))
            elif inferred_market == "NZ":
                updates.append(
                    (
                        row_idx,
                        price_idx + 1,
                        f'=IFERROR(NZX_PRICE({ticker_ref}),IFERROR(GOOGLEFINANCE("NZE:"&{ticker_ref},"price"),""))',
                    )
                )

        if shares_idx is not None and price_idx is not None and _is_blank(row, shares_idx):
            if inferred_market == "US":
                price_ref = f"${_col_letter(price_idx + 1)}{row_idx}"
                formula = (
                    f'=IF({price_ref}="","",'
                    f'IFERROR(VALUE(REGEXREPLACE(TO_TEXT(GOOGLEFINANCE("NASDAQ:"&{ticker_ref},"marketcap")),"[^0-9.Ee+-]",""))/{price_ref}/1e9,'
                    f'IFERROR(VALUE(REGEXREPLACE(TO_TEXT(GOOGLEFINANCE("NYSE:"&{ticker_ref},"marketcap")),"[^0-9.Ee+-]",""))/{price_ref}/1e9,"")))'
                )
                updates.append((row_idx, shares_idx + 1, formula))

        if net_cash_idx is not None and _is_blank(row, net_cash_idx):
            updates.append((row_idx, net_cash_idx + 1, 0))

        if g_idx is not None and _is_blank(row, g_idx):
            updates.append((row_idx, g_idx + 1, 0.06))

        if n_idx is not None and _is_blank(row, n_idx):
            updates.append((row_idx, n_idx + 1, 5))

        if g_terminal_idx is not None and _is_blank(row, g_terminal_idx):
            updates.append((row_idx, g_terminal_idx + 1, 0.03))

        if links_idx is not None and _is_blank(row, links_idx):
            code = _ticker_for_link(ticker)
            if inferred_market == "US":
                updates.append((row_idx, links_idx + 1, f"https://stockanalysis.com/stocks/{code.lower()}/"))
            elif inferred_market == "NZ":
                updates.append((row_idx, links_idx + 1, f"https://www.nzx.com/instruments/{code}"))

    if updates:
        cells = [ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cells, updates):
            cell.value = value
        ws.update_cells(cells, value_input_option="USER_ENTERED")
    return len(updates)


def _append_links(existing: str, urls: list[str]) -> str:
    if not urls:
        return str(existing or "").strip()
    existing_text = str(existing or "").strip()
    new_urls = []
    for url in urls:
        if not url:
            continue
        if existing_text and url in existing_text:
            continue
        new_urls.append(url)
    if not new_urls:
        return existing_text
    if existing_text:
        return f"{existing_text} | {' | '.join(new_urls)}"
    return " | ".join(new_urls)


def _ensure_sources_log(spreadsheet):
    tab_name = "SOURCES_LOG"
    headers = ["Timestamp", "Ticker", "Field", "Value", "SourceURL"]
    for ws in spreadsheet.worksheets():
        if ws.title == tab_name:
            if not ws.row_values(1):
                ws.update(range_name="A1", values=[headers])
                ws.freeze(rows=1)
            return ws
    ws = spreadsheet.add_worksheet(title=tab_name, rows=500, cols=len(headers) + 2)
    ws.update(range_name="A1", values=[headers])
    ws.freeze(rows=1)
    return ws


def _append_sources_log(spreadsheet, rows: list[list[str]]):
    if not rows:
        return
    ws = _ensure_sources_log(spreadsheet)
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def _is_missing_company(value: str, ticker: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text in {"-", "N/A", "n/a"}:
        return True
    return text.strip().lower() == str(ticker or "").strip().lower()


def _is_missing_shares(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    try:
        numeric = float(text.replace(",", ""))
    except ValueError:
        return True
    return numeric == 0.0


def autofill_nz_fundamentals_from_sources(inputs_ws, spreadsheet, sources_tab: str = "Sources") -> dict:
    values = inputs_ws.get_all_values()
    if not values:
        return {
            "results": [{"status": "error", "ticker": "", "message": "APP_INPUTS is empty."}],
            "tickers_updated": [],
            "fields_updated": {},
            "tickers_missing_data": [],
            "warnings": [],
        }

    headers = values[0]
    header_map = build_header_map(headers)
    ticker_idx = header_map.get("Ticker")
    market_idx = header_map.get("Market")
    company_idx = header_map.get("Company")
    shares_idx = header_map.get("Shares_bn")
    net_cash_idx = header_map.get("Net cash/(debt) (bn)")
    fcf1_idx = header_map.get("FCF1 (next-year, bn)")
    links_idx = header_map.get("Links")

    if ticker_idx is None or market_idx is None:
        return {
            "results": [{"status": "error", "ticker": "", "message": "APP_INPUTS missing Ticker/Market columns."}],
            "tickers_updated": [],
            "fields_updated": {},
            "tickers_missing_data": [],
            "warnings": [],
        }

    sources_entries = load_sources_entries(spreadsheet, sources_tab)

    updates: list[tuple[int, int, str | float]] = []
    results: list[dict] = []
    warnings: list[str] = []
    tickers_updated: list[str] = []
    tickers_missing_data: list[str] = []
    fields_updated = {"net_cash": 0, "fcf1": 0, "links": 0, "company": 0, "shares": 0}
    log_rows: list[list[str]] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for row_idx, row in enumerate(values[1:], start=2):
        ticker = _get_cell(row, ticker_idx)
        if not ticker:
            continue
        market = _get_cell(row, market_idx).upper()
        if market != "NZ":
            continue

        company_value = _get_cell(row, company_idx) if company_idx is not None else ""
        shares_value = _get_cell(row, shares_idx) if shares_idx is not None else ""
        company_blank = company_idx is not None and _is_missing_company(company_value, ticker)
        shares_blank = shares_idx is not None and _is_missing_shares(shares_value)
        net_cash_blank = net_cash_idx is not None and _is_blank(row, net_cash_idx)
        fcf1_blank = fcf1_idx is not None and _is_blank(row, fcf1_idx)
        if not net_cash_blank and not fcf1_blank and not company_blank and not shares_blank and links_idx is None:
            results.append({"status": "ok", "ticker": ticker, "message": "skipped"})
            continue

        snapshot = None
        if company_blank or shares_blank:
            try:
                snapshot = get_nzx_snapshot(ticker)
            except Exception as exc:
                warnings.append(f"{ticker}: NZX snapshot failed ({exc})")

        lookup = lookup_sources(sources_entries, ticker)
        matched_key = lookup.get("matched_key")
        used_shares = lookup.get("used_shares_rows")
        if used_shares:
            warnings.append(f"{ticker}: matched shares-only row in Sources")

        updated_fields = []
        missing_fields = []

        if company_blank and snapshot and snapshot.get("company"):
            updates.append((row_idx, company_idx + 1, snapshot.get("company")))
            fields_updated["company"] += 1
            updated_fields.append("company")
            log_rows.append(
                [
                    timestamp,
                    ticker,
                    "Company",
                    str(snapshot.get("company")),
                    snapshot.get("source_url"),
                ]
            )
        elif company_blank:
            missing_fields.append("company")

        if shares_blank and snapshot and snapshot.get("shares_bn") is not None:
            updates.append((row_idx, shares_idx + 1, snapshot.get("shares_bn")))
            fields_updated["shares"] += 1
            updated_fields.append("shares")
            log_rows.append(
                [
                    timestamp,
                    ticker,
                    "Shares_bn",
                    str(snapshot.get("shares_bn")),
                    snapshot.get("source_url"),
                ]
            )
        elif shares_blank:
            missing_fields.append("shares")

        if net_cash_blank:
            netcash_bn = lookup.get("netcash_bn")
            if netcash_bn is not None and net_cash_idx is not None:
                updates.append((row_idx, net_cash_idx + 1, netcash_bn))
                fields_updated["net_cash"] += 1
                updated_fields.append("net_cash")
            else:
                missing_fields.append("net_cash")

        if fcf1_blank:
            fcf1_bn = lookup.get("fcf1_bn")
            if fcf1_bn is not None and fcf1_idx is not None:
                updates.append((row_idx, fcf1_idx + 1, fcf1_bn))
                fields_updated["fcf1"] += 1
                updated_fields.append("fcf1")
            else:
                missing_fields.append("fcf1")

        if links_idx is not None:
            link_value = _get_cell(row, links_idx)
            new_links = _append_links(link_value, lookup.get("urls", []))
            if new_links != str(link_value or "").strip():
                updates.append((row_idx, links_idx + 1, new_links))
                fields_updated["links"] += 1
                updated_fields.append("links")

        if updated_fields:
            tickers_updated.append(ticker)
            results.append({"status": "ok", "ticker": ticker, "message": "updated", "updated_fields": updated_fields})
        elif missing_fields and matched_key is None:
            tickers_missing_data.append(ticker)
            results.append({"status": "ok", "ticker": ticker, "message": "missing sources"})
        elif missing_fields:
            tickers_missing_data.append(ticker)
            results.append({"status": "ok", "ticker": ticker, "message": "missing data"})
        else:
            results.append({"status": "ok", "ticker": ticker, "message": "skipped"})

    if updates:
        cells = [inputs_ws.cell(r, c) for r, c, _ in updates]
        for cell, (_, _, value) in zip(cells, updates):
            cell.value = value
        inputs_ws.update_cells(cells, value_input_option="USER_ENTERED")
    _append_sources_log(spreadsheet, log_rows)

    return {
        "results": results,
        "tickers_updated": tickers_updated,
        "fields_updated": fields_updated,
        "tickers_missing_data": tickers_missing_data,
        "warnings": warnings,
    }
