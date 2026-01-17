import json
import time
from typing import Optional, Tuple

import requests


_SEC_HEADERS = {
    "User-Agent": "NZWealthManagerPro/1.0 (support@example.com)"
}


def fetch_sec_company_tickers() -> dict:
    url = "https://www.sec.gov/files/company_tickers.json"
    time.sleep(0.2)
    resp = requests.get(url, headers=_SEC_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_cik_for_ticker(ticker: str, tickers_json: dict) -> Optional[str]:
    symbol = ticker.strip().upper()
    for entry in tickers_json.values():
        if str(entry.get("ticker", "")).strip().upper() == symbol:
            cik = str(entry.get("cik_str", "")).strip()
            return cik.zfill(10) if cik.isdigit() else None
    return None


def fetch_sec_companyfacts(cik: str) -> dict:
    padded = cik.zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json"
    time.sleep(0.2)
    resp = requests.get(url, headers=_SEC_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _latest_from_units(units: dict) -> Tuple[Optional[float], Optional[str]]:
    if not units:
        return None, None
    currency = "USD" if "USD" in units else next(iter(units.keys()))
    items = units.get(currency, [])
    if not items:
        return None, None
    items = sorted(items, key=lambda x: x.get("end") or x.get("filed") or "")
    value = items[-1].get("val")
    return value, currency


def _latest_tag_value(facts: dict, tag: str) -> Tuple[Optional[float], Optional[str]]:
    tag_data = facts.get(tag)
    if not tag_data:
        return None, None
    return _latest_from_units(tag_data.get("units", {}))


def compute_net_cash_from_companyfacts(facts_json: dict) -> Tuple[Optional[float], Optional[str]]:
    facts = (facts_json.get("facts") or {}).get("us-gaap", {})

    cash_tags = [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ]
    investment_tags = ["ShortTermInvestments", "MarketableSecuritiesCurrent"]
    debt_current_tags = ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"]
    debt_long_tags = ["LongTermDebt", "LongTermDebtNoncurrent"]

    cash_val = cash_ccy = None
    for tag in cash_tags:
        cash_val, cash_ccy = _latest_tag_value(facts, tag)
        if cash_val is not None:
            break

    inv_val = inv_ccy = None
    for tag in investment_tags:
        inv_val, inv_ccy = _latest_tag_value(facts, tag)
        if inv_val is not None:
            break

    debt_current_val = debt_current_ccy = None
    for tag in debt_current_tags:
        debt_current_val, debt_current_ccy = _latest_tag_value(facts, tag)
        if debt_current_val is not None:
            break

    debt_long_val = debt_long_ccy = None
    for tag in debt_long_tags:
        debt_long_val, debt_long_ccy = _latest_tag_value(facts, tag)
        if debt_long_val is not None:
            break

    currencies = [c for c in [cash_ccy, inv_ccy, debt_current_ccy, debt_long_ccy] if c]
    if not currencies:
        return None, None
    currency = currencies[0]

    def _match_currency(val, ccy):
        if val is None:
            return None
        return val if ccy == currency else None

    cash = _match_currency(cash_val, cash_ccy) or 0.0
    inv = _match_currency(inv_val, inv_ccy) or 0.0
    debt_current = _match_currency(debt_current_val, debt_current_ccy) or 0.0
    debt_long = _match_currency(debt_long_val, debt_long_ccy) or 0.0

    if cash == 0.0 and inv == 0.0 and debt_current == 0.0 and debt_long == 0.0:
        return None, currency

    net_cash = cash + inv - (debt_current + debt_long)
    return net_cash, currency
