from __future__ import annotations

import os
import time
from typing import Any

import requests
import streamlit as st

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT_HELP = (
    'Missing SEC user agent. Add `sec_user_agent = "AppName your@email.com"` to '
    ".streamlit/secrets.toml (and Streamlit Cloud secrets)."
)
_CIK_CACHE_TTL_S = 24 * 60 * 60
_cik_cache: dict[str, dict[str, dict[str, str]]] = {}
_cik_cache_ts: dict[str, float] = {}


def get_sec_user_agent() -> str | None:
    user_agent = str(st.secrets.get("sec_user_agent", "")).strip()
    if not user_agent:
        user_agent = str(os.environ.get("SEC_USER_AGENT", "")).strip()
    return user_agent or None


def _build_sec_headers(user_agent: str, host: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": host,
    }


def get_sec_headers(user_agent: str | None = None) -> dict[str, str]:
    if not user_agent:
        user_agent = get_sec_user_agent()
    if not user_agent:
        raise RuntimeError(SEC_USER_AGENT_HELP)
    return _build_sec_headers(user_agent, "data.sec.gov")


def _fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_ticker_cik_map(user_agent: str) -> dict[str, dict[str, str]]:
    now = time.time()
    cached = _cik_cache.get(user_agent)
    cached_ts = _cik_cache_ts.get(user_agent, 0.0)
    if cached and now - cached_ts < _CIK_CACHE_TTL_S:
        return cached

    headers = _build_sec_headers(user_agent, "www.sec.gov")
    data = _fetch_json(SEC_TICKER_URL, headers)
    mapping: dict[str, dict[str, str]] = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).strip().upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        name = str(entry.get("title", "")).strip()
        if ticker and cik:
            mapping[ticker] = {"cik": cik, "name": name}
    _cik_cache[user_agent] = mapping
    _cik_cache_ts[user_agent] = now
    return mapping


def _latest_annual_value(facts: dict, tag: str) -> float | None:
    units = (facts.get("us-gaap", {}).get(tag, {}) or {}).get("units", {})
    entries = []
    for unit_values in units.values():
        for item in unit_values:
            form = str(item.get("form", "")).upper()
            fp = str(item.get("fp", "")).upper()
            fy = item.get("fy")
            val = item.get("val")
            if form not in {"10-K", "10-K/A"} or fp != "FY":
                continue
            if val is None:
                continue
            entries.append((fy or 0, item.get("end", ""), val))
    if not entries:
        return None
    entries.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return float(entries[0][2])


def _first_available_value(facts: dict, tags: list[str]) -> float | None:
    for tag in tags:
        value = _latest_annual_value(facts, tag)
        if value is not None:
            return value
    return None


def _shares_outstanding(facts: dict) -> float | None:
    tags = [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesIssued",
        "SharesOutstanding",
    ]
    return _first_available_value(facts, tags)


def _net_cash_debt_bn_from_facts(facts: dict) -> float | None:
    cash = _first_available_value(
        facts,
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalents",
        ],
    )
    short_term_investments = _first_available_value(
        facts,
        [
            "ShortTermInvestments",
            "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesCurrent",
        ],
    )
    debt_total = _first_available_value(facts, ["Debt"])
    debt_current = _first_available_value(
        facts,
        [
            "DebtCurrent",
            "ShortTermBorrowings",
            "CurrentPortionOfLongTermDebt",
            "LongTermDebtCurrent",
        ],
    )
    debt_long = _first_available_value(
        facts,
        [
            "LongTermDebt",
            "LongTermDebtNoncurrent",
            "LongTermBorrowings",
        ],
    )

    cash_total = 0.0
    has_cash = False
    for value in (cash, short_term_investments):
        if value is not None:
            cash_total += value
            has_cash = True

    if debt_total is None:
        debt_total = (debt_current or 0.0) + (debt_long or 0.0)
    if not has_cash and debt_total == 0.0:
        return None
    return (cash_total - debt_total) / 1e9


def get_net_cash_debt_bn(ticker: str, headers: dict[str, str], throttle_s: float = 0.2) -> float | None:
    mapping = load_ticker_cik_map(headers["User-Agent"])
    info = mapping.get(ticker)
    if not info:
        raise RuntimeError(f"CIK not found for {ticker}")
    cik = info["cik"]

    time.sleep(throttle_s)
    facts = _fetch_json(SEC_FACTS_URL.format(cik=cik), headers).get("facts", {})
    return _net_cash_debt_bn_from_facts(facts)


def fetch_us_fundamentals(ticker: str, headers: dict[str, str], throttle_s: float = 0.2) -> dict[str, float | str | None]:
    mapping = load_ticker_cik_map(headers["User-Agent"])
    info = mapping.get(ticker)
    if not info:
        raise RuntimeError(f"CIK not found for {ticker}")
    cik = info["cik"]
    company_name = info.get("name") or None

    time.sleep(throttle_s)
    facts = _fetch_json(SEC_FACTS_URL.format(cik=cik), headers).get("facts", {})

    shares = _shares_outstanding(facts)

    cfo = _first_available_value(facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _first_available_value(
        facts,
        ["PaymentsToAcquirePropertyPlantAndEquipment", "CapitalExpenditures"],
    )

    net_cash_bn = _net_cash_debt_bn_from_facts(facts)
    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - capex

    return {
        "cik": cik,
        "company_name": company_name,
        "shares_bn": None if shares is None else shares / 1e9,
        "net_cash_bn": net_cash_bn,
        "fcf_bn": None if fcf is None else fcf / 1e9,
        "ocf": cfo,
        "capex": capex,
    }
