from dataclasses import dataclass
import json
import re
import time
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup
import yfinance as yf


_PRICE_RE = re.compile(r"-?\d+(?:\.\d+)?")
_NZX_JSON_VALUE_RE = r"\"{key}\"\\s*:\\s*\"?(?P<val>-?\\d+(?:\\.\\d+)?)\"?"


@dataclass(frozen=True)
class Quote:
    ticker: str
    market: str
    price: Optional[float]
    currency: Optional[str]
    source: str
    url: Optional[str] = None
    status_code: Optional[int] = None
    error_snippet: Optional[str] = None
    raw_price: Optional[str] = None
    raw_shares: Optional[str] = None
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    shares_outstanding: Optional[float] = None
    market_cap: Optional[float] = None


def _parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    match = _PRICE_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_int(value: str) -> Optional[int]:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d\-]", "", str(value))
    if cleaned in ("", "-", "--"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _extract_label_value(soup: BeautifulSoup, label: str) -> Optional[str]:
    target = soup.find(string=re.compile(rf"^{re.escape(label)}$", re.I))
    if not target:
        return None

    if target.parent and target.parent.name == "dt":
        sibling = target.parent.find_next_sibling("dd")
        if sibling:
            return sibling.get_text(strip=True)

    if target.parent and target.parent.name in {"th", "td"}:
        sibling = target.parent.find_next_sibling("td")
        if sibling:
            return sibling.get_text(strip=True)

    row = target.parent.find_parent("tr") if target.parent else None
    if row:
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            return cells[1].get_text(strip=True)

    return None


def _deep_find_key_value(obj, target_key: str) -> Optional[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) == target_key and v not in (None, ""):
                return str(v)
            found = _deep_find_key_value(v, target_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_key_value(item, target_key)
            if found is not None:
                return found
    return None


def _regex_nzx_value(text: str, key: str) -> Optional[str]:
    if not text:
        return None
    pattern = _NZX_JSON_VALUE_RE.format(key=re.escape(key))
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group("val")


def _regex_nzx_last_price(text: str) -> Optional[str]:
    for key in ("lastPrice", "lastTradePrice", "lastTradedPrice", "priceAmount", "closePrice"):
        value = _regex_nzx_value(text, key)
        if value is not None:
            return value
    return None


def _regex_nzx_shares_issued(text: str) -> Optional[str]:
    if not text:
        return None
    table_match = re.search(
        r"Securities\\s+Issued\\s*</th>\\s*<td>([^<]+)</td>",
        text,
        flags=re.I,
    )
    if table_match:
        return table_match.group(1)
    for key in ("securitiesIssued", "sharesIssued", "securities_issued", "shares_issued"):
        value = _regex_nzx_value(text, key)
        if value is not None:
            return value
    return None


def parse_google_finance_html(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")

    data_price = soup.find(attrs={"data-last-price": True})
    if data_price and data_price.get("data-last-price"):
        return _parse_float(data_price.get("data-last-price"))

    price_node = soup.select_one(".YMlKec.fxKbKc")
    if price_node:
        return _parse_float(price_node.get_text())

    meta_price = soup.find("meta", attrs={"property": "og:price:amount"})
    if meta_price and meta_price.get("content"):
        return _parse_float(meta_price.get("content"))

    return None


def parse_nzx_next_data(html: str) -> dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {
            "price": _regex_nzx_last_price(html),
            "shares": _regex_nzx_shares_issued(html),
        }
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return {
            "price": _regex_nzx_last_price(script.string),
            "shares": _regex_nzx_shares_issued(script.string),
        }

    price_key_order = [
        "lastPrice",
        "lastTradePrice",
        "lastTradedPrice",
        "last_trade_price",
        "priceAmount",
        "closePrice",
        "price",
    ]
    shares_key_order = [
        "securitiesIssued",
        "sharesIssued",
        "securities_issued",
        "shares_issued",
    ]

    price_val = None
    for key in price_key_order:
        price_val = _deep_find_key_value(data, key)
        if price_val is not None:
            break
    if price_val is None:
        price_val = _regex_nzx_last_price(script.string)

    shares_val = None
    for key in shares_key_order:
        shares_val = _deep_find_key_value(data, key)
        if shares_val is not None:
            break
    if shares_val is None:
        shares_val = _regex_nzx_shares_issued(script.string)

    return {
        "price": str(price_val) if price_val is not None else None,
        "shares": str(shares_val) if shares_val is not None else None,
    }


def parse_nzx_instrument_html(html: str) -> dict[str, Optional[float]]:
    soup = BeautifulSoup(html, "html.parser")

    next_data = parse_nzx_next_data(html)
    price_raw = next_data.get("price")
    shares_raw = next_data.get("shares")
    price = _parse_float(price_raw) if price_raw else parse_nzx_html(html)
    securities_text = _extract_label_value(soup, "Securities Issued")
    securities_issued = _parse_int(securities_text) if securities_text else _parse_int(shares_raw)
    open_price = _parse_float(_extract_label_value(soup, "Open"))
    high_price = _parse_float(_extract_label_value(soup, "High"))
    low_price = _parse_float(_extract_label_value(soup, "Low"))
    pe_ratio = _parse_float(_extract_label_value(soup, "P/E"))

    div_yield_text = _extract_label_value(soup, "Gross Div Yield")
    div_yield = _parse_float(div_yield_text) if div_yield_text else None

    cap_text = _extract_label_value(soup, "Capitalisation (000s)")
    cap_value = _parse_float(cap_text)
    market_cap = cap_value * 1000 if cap_value is not None else None

    return {
        "price": price,
        "raw_price": price_raw,
        "raw_shares": shares_raw,
        "shares_outstanding": float(securities_issued) if securities_issued is not None else None,
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
        "pe_ratio": pe_ratio,
        "dividend_yield": div_yield,
        "market_cap": market_cap,
    }


def parse_nzx_html(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")

    price_node = soup.find(attrs={"data-testid": "instrument-price"})
    if price_node:
        return _parse_float(price_node.get_text())

    meta_price = soup.find("meta", attrs={"property": "og:price:amount"})
    if meta_price and meta_price.get("content"):
        return _parse_float(meta_price.get("content"))

    headline = soup.select_one("[class*='price']")
    if headline:
        return _parse_float(headline.get_text())

    return None


def fetch_us_quote_google_finance(ticker: str, exchange: Optional[str] = None) -> Quote:
    symbol = ticker.strip().upper()
    if ":" in symbol:
        query = symbol
    elif exchange:
        query = f"{symbol}:{exchange.strip().upper()}"
    else:
        query = f"{symbol}:NASDAQ"

    url = f"https://www.google.com/finance/quote/{query}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    price = parse_google_finance_html(resp.text)
    return Quote(ticker=symbol, market="US", price=price, currency="USD", source="google_finance")


def fetch_us_quote_yfinance(ticker: str) -> Quote:
    symbol = ticker.strip().upper()
    info = yf.Ticker(symbol).info
    price = info.get("regularMarketPrice")
    if price is None:
        price = info.get("currentPrice")

    return Quote(
        ticker=symbol,
        market="US",
        price=price,
        currency=info.get("currency", "USD"),
        source="yfinance",
        open_price=info.get("open"),
        high_price=info.get("dayHigh"),
        low_price=info.get("dayLow"),
        pe_ratio=info.get("trailingPE"),
        dividend_yield=(info.get("dividendYield") or 0) * 100 if info.get("dividendYield") else None,
        shares_outstanding=info.get("sharesOutstanding"),
        market_cap=info.get("marketCap"),
    )


def fetch_us_quote(ticker: str, exchange: Optional[str] = None) -> Quote:
    base_quote = None
    try:
        base_quote = fetch_us_quote_google_finance(ticker, exchange=exchange)
    except Exception:
        base_quote = Quote(
            ticker=ticker.strip().upper(),
            market="US",
            price=None,
            currency="USD",
            source="google_finance",
        )

    yf_quote = None
    try:
        yf_quote = fetch_us_quote_yfinance(ticker)
    except Exception:
        yf_quote = None

    if yf_quote:
        price = base_quote.price if base_quote and base_quote.price is not None else yf_quote.price
        source = "google_finance+yfinance" if base_quote and base_quote.price is not None else "yfinance"
        return Quote(
            ticker=yf_quote.ticker,
            market="US",
            price=price,
            currency=yf_quote.currency,
            source=source,
            open_price=yf_quote.open_price,
            high_price=yf_quote.high_price,
            low_price=yf_quote.low_price,
            pe_ratio=yf_quote.pe_ratio,
            dividend_yield=yf_quote.dividend_yield,
            shares_outstanding=yf_quote.shares_outstanding,
        )

    if base_quote:
        return base_quote

    return Quote(ticker=ticker.strip().upper(), market="US", price=None, currency="USD", source="google_finance")


def fetch_nz_quote_nzx(ticker: str) -> Quote:
    symbol = ticker.strip().upper()
    url = f"https://www.nzx.com/instruments/{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = None
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    if resp is None:
        raise last_exc or RuntimeError("NZX request failed")

    data = parse_nzx_instrument_html(resp.text)
    snippet = None
    if data.get("price") is None:
        snippet = resp.text[:200]
    return Quote(
        ticker=symbol,
        market="NZ",
        price=data.get("price"),
        currency="NZD",
        source="nzx",
        url=url,
        status_code=resp.status_code,
        error_snippet=snippet,
        raw_price=data.get("raw_price"),
        raw_shares=data.get("raw_shares"),
        open_price=data.get("open_price"),
        high_price=data.get("high_price"),
        low_price=data.get("low_price"),
        pe_ratio=data.get("pe_ratio"),
        dividend_yield=data.get("dividend_yield"),
        shares_outstanding=data.get("shares_outstanding"),
        market_cap=data.get("market_cap"),
    )


def fetch_nz_quote_fallback(ticker: str, fallback_prices: dict[str, float]) -> Quote:
    symbol = ticker.strip().upper()
    price = fallback_prices.get(symbol)
    return Quote(ticker=symbol, market="NZ", price=price, currency="NZD", source="nzx_sheet")


def fetch_nz_quote(ticker: str, fallback_prices: Optional[dict[str, float]] = None) -> Quote:
    try:
        nzx_quote = fetch_nz_quote_nzx(ticker)
        if nzx_quote.price is None and fallback_prices is not None:
            return fetch_nz_quote_fallback(ticker, fallback_prices)
        return nzx_quote
    except Exception:
        if fallback_prices is not None:
            return fetch_nz_quote_fallback(ticker, fallback_prices)
        raise
