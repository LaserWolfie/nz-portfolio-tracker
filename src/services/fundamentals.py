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


def _first_available_tag_value(facts: dict, tags: list[str]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    for tag in tags:
        value, currency = _latest_tag_value(facts, tag)
        if value is not None:
            return value, currency, tag
    return None, None, None


def compute_net_cash_from_companyfacts(facts_json: dict) -> Tuple[Optional[float], Optional[str]]:
    facts = (facts_json.get("facts") or {}).get("us-gaap", {})

    cash_tags = [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
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


def compute_net_cash_from_companyfacts_detailed(
    facts_json: dict,
) -> Tuple[Optional[float], Optional[str], dict]:
    facts = (facts_json.get("facts") or {}).get("us-gaap", {})

    cash_tags = [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
    ]
    investment_tags = ["ShortTermInvestments", "MarketableSecuritiesCurrent"]
    debt_current_tags = ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"]
    debt_long_tags = ["LongTermDebt", "LongTermDebtNoncurrent"]

    cash_val = cash_ccy = cash_tag = None
    for tag in cash_tags:
        cash_val, cash_ccy = _latest_tag_value(facts, tag)
        if cash_val is not None:
            cash_tag = tag
            break

    inv_val = inv_ccy = inv_tag = None
    for tag in investment_tags:
        inv_val, inv_ccy = _latest_tag_value(facts, tag)
        if inv_val is not None:
            inv_tag = tag
            break

    debt_current_val = debt_current_ccy = debt_current_tag = None
    for tag in debt_current_tags:
        debt_current_val, debt_current_ccy = _latest_tag_value(facts, tag)
        if debt_current_val is not None:
            debt_current_tag = tag
            break

    debt_long_val = debt_long_ccy = debt_long_tag = None
    for tag in debt_long_tags:
        debt_long_val, debt_long_ccy = _latest_tag_value(facts, tag)
        if debt_long_val is not None:
            debt_long_tag = tag
            break

    currencies = [c for c in [cash_ccy, inv_ccy, debt_current_ccy, debt_long_ccy] if c]
    if not currencies:
        return None, None, {
            "cash_tags": cash_tags,
            "investment_tags": investment_tags,
            "debt_current_tags": debt_current_tags,
            "debt_long_tags": debt_long_tags,
            "cash_tag": cash_tag,
            "inv_tag": inv_tag,
            "debt_current_tag": debt_current_tag,
            "debt_long_tag": debt_long_tag,
            "cash_val": cash_val,
            "inv_val": inv_val,
            "debt_current_val": debt_current_val,
            "debt_long_val": debt_long_val,
            "currency": None,
        }
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
        return None, currency, {
            "cash_tags": cash_tags,
            "investment_tags": investment_tags,
            "debt_current_tags": debt_current_tags,
            "debt_long_tags": debt_long_tags,
            "cash_tag": cash_tag,
            "inv_tag": inv_tag,
            "debt_current_tag": debt_current_tag,
            "debt_long_tag": debt_long_tag,
            "cash_val": cash_val,
            "inv_val": inv_val,
            "debt_current_val": debt_current_val,
            "debt_long_val": debt_long_val,
            "currency": currency,
        }

    net_cash = cash + inv - (debt_current + debt_long)
    return net_cash, currency, {
        "cash_tags": cash_tags,
        "investment_tags": investment_tags,
        "debt_current_tags": debt_current_tags,
        "debt_long_tags": debt_long_tags,
        "cash_tag": cash_tag,
        "inv_tag": inv_tag,
        "debt_current_tag": debt_current_tag,
        "debt_long_tag": debt_long_tag,
        "cash_val": cash_val,
        "inv_val": inv_val,
        "debt_current_val": debt_current_val,
        "debt_long_val": debt_long_val,
        "currency": currency,
    }


def compute_cashflow_from_companyfacts(
    facts_json: dict,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str]]:
    facts = (facts_json.get("facts") or {}).get("us-gaap", {})
    cfo_tags = [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ]
    capex_tags = [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpenditures",
    ]

    cfo, cfo_ccy, _ = _first_available_tag_value(facts, cfo_tags)
    capex, capex_ccy, _ = _first_available_tag_value(facts, capex_tags)

    if cfo is None and capex is None:
        return None, None, None, None

    currency = cfo_ccy or capex_ccy
    if currency and (cfo_ccy and capex_ccy) and (cfo_ccy != capex_ccy):
        return None, None, None, None

    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - capex
    return cfo, capex, fcf, currency


def compute_cashflow_from_companyfacts_detailed(
    facts_json: dict,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[str], dict]:
    facts = (facts_json.get("facts") or {}).get("us-gaap", {})
    cfo_tags = [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ]
    capex_tags = [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpenditures",
    ]

    cfo, cfo_ccy, cfo_tag = _first_available_tag_value(facts, cfo_tags)
    capex, capex_ccy, capex_tag = _first_available_tag_value(facts, capex_tags)

    if cfo is None and capex is None:
        return None, None, None, None, {
            "cfo_tags": cfo_tags,
            "capex_tags": capex_tags,
            "cfo_tag": cfo_tag,
            "capex_tag": capex_tag,
            "cfo_val": cfo,
            "capex_val": capex,
            "currency": None,
        }

    currency = cfo_ccy or capex_ccy
    if currency and (cfo_ccy and capex_ccy) and (cfo_ccy != capex_ccy):
        return None, None, None, None, {
            "cfo_tags": cfo_tags,
            "capex_tags": capex_tags,
            "cfo_tag": cfo_tag,
            "capex_tag": capex_tag,
            "cfo_val": cfo,
            "capex_val": capex,
            "currency": currency,
            "error": "currency_mismatch",
        }

    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - capex
    return cfo, capex, fcf, currency, {
        "cfo_tags": cfo_tags,
        "capex_tags": capex_tags,
        "cfo_tag": cfo_tag,
        "capex_tag": capex_tag,
        "cfo_val": cfo,
        "capex_val": capex,
        "currency": currency,
    }
