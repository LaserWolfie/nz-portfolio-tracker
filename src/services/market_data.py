from dataclasses import dataclass
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup
import yfinance as yf


_PRICE_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Quote:
    ticker: str
    market: str
    price: Optional[float]
    currency: Optional[str]
    source: str
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    pe_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    shares_outstanding: Optional[float] = None


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
    )


def fetch_us_quote(ticker: str, exchange: Optional[str] = None) -> Quote:
    try:
        return fetch_us_quote_google_finance(ticker, exchange=exchange)
    except Exception:
        return fetch_us_quote_yfinance(ticker)


def fetch_nz_quote_nzx(ticker: str) -> Quote:
    symbol = ticker.strip().upper()
    url = f"https://www.nzx.com/instruments/{symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    price = parse_nzx_html(resp.text)
    return Quote(ticker=symbol, market="NZ", price=price, currency="NZD", source="nzx")


def fetch_nz_quote_fallback(ticker: str, fallback_prices: dict[str, float]) -> Quote:
    symbol = ticker.strip().upper()
    price = fallback_prices.get(symbol)
    return Quote(ticker=symbol, market="NZ", price=price, currency="NZD", source="nzx_sheet")


def fetch_nz_quote(ticker: str, fallback_prices: Optional[dict[str, float]] = None) -> Quote:
    try:
        return fetch_nz_quote_nzx(ticker)
    except Exception:
        if fallback_prices is not None:
            return fetch_nz_quote_fallback(ticker, fallback_prices)
        raise
