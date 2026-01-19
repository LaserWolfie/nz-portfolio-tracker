from __future__ import annotations

import re
import time
from typing import Any

import requests

_UA = "NZ Wealth Manager Pro (contact: support@example.com)"


def _clean_ticker(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
    return text


def _extract_number(text: str) -> int | None:
    match = re.search(r"([0-9][0-9,]*)", text or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = str(text).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _clean_instrument_name(name: str | None) -> str | None:
    if not name:
        return None
    text = re.sub(r"\s+", " ", name).strip()
    suffixes = [
        "Ordinary Shares",
        "Ordinary Share",
        "Ordinary",
        "Rights",
    ]
    for suffix in suffixes:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip(" -")
    return text or None


def _parse_nzx_kv_pairs(html: str) -> dict[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    pairs: dict[str, str] = {}

    for dt in soup.find_all("dt"):
        label = dt.get_text(" ", strip=True)
        if not label:
            continue
        dd = dt.find_next_sibling("dd")
        if dd:
            value = dd.get_text(" ", strip=True)
            if value and label not in pairs:
                pairs[label] = value

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            if label and value and label not in pairs:
                pairs[label] = value

    return pairs


def _parse_snapshot(html: str) -> dict[str, Any]:
    kv_pairs = _parse_nzx_kv_pairs(html)
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = re.sub(r"\s+", " ", html)

    shares = None
    match = re.search(r"Securities Issued\s*([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    if match:
        shares = _extract_number(match.group(1))
    if shares is None:
        shares_text = kv_pairs.get("Securities Issued")
        if shares_text:
            shares = _extract_number(shares_text)

    company = None
    instrument_name = None
    company_source = None
    shares_source = "direct" if shares is not None else None
    if "BeautifulSoup" in globals():
        label = soup.find(string=re.compile(r"Issued By", re.IGNORECASE))
        if label:
            container = label.parent
            if container:
                value = container.find_next(["a", "span", "div", "dd"])
                if value:
                    company = value.get_text(" ", strip=True)
                    company_source = company_source or "direct"
        if not company:
            label = soup.find(string=re.compile(r"Instrument Name", re.IGNORECASE))
            if label:
                container = label.parent
                if container:
                    value = container.find_next(["a", "span", "div", "dd"])
                    if value:
                        instrument_name = value.get_text(" ", strip=True)
        if not instrument_name:
            h1 = soup.find("h1")
            if h1:
                instrument_name = h1.get_text(" ", strip=True)
    if not company:
        match = re.search(r"Issued By\s*([A-Za-z0-9&\-\.,\s]+?)\s(?:Securities Issued|Instrument Name|$)", text, flags=re.IGNORECASE)
        if match:
            company = match.group(1).strip()
            company_source = company_source or "direct"
    if not instrument_name:
        match = re.search(r"Instrument Name\s*([A-Za-z0-9&\-\.,\s]+?)\s(?:Securities Issued|Issued By|$)", text, flags=re.IGNORECASE)
        if match:
            instrument_name = match.group(1).strip()

    if not company:
        issued_by = kv_pairs.get("Issued By")
        if issued_by:
            company = issued_by
            company_source = "issued_by"
    if not instrument_name:
        instrument_name = kv_pairs.get("Instrument Name")

    if not company:
        company = _clean_instrument_name(instrument_name)
        if company:
            company_source = "instrument_name"

    cap_thousands = None
    price = None
    if "Capitalisation (000s)" in kv_pairs:
        cap_thousands = _parse_float(kv_pairs.get("Capitalisation (000s)"))
    for price_key in ("Price", "Last Price", "Last Trade Price", "Last Traded Price"):
        if price_key in kv_pairs:
            price = _parse_float(kv_pairs.get(price_key))
            if price is not None:
                break

    if shares is None and cap_thousands is not None and price:
        shares = int((cap_thousands * 1000) / price)
        shares_source = "fallback_cap_price"

    if shares is not None and shares_source is None:
        shares_source = "direct"

    return {
        "company": company,
        "shares_outstanding": shares,
        "company_source": company_source,
        "shares_source": shares_source,
        "price": price,
        "cap_thousands": cap_thousands,
    }


def get_nzx_snapshot(ticker: str, throttle_s: float = 0.3) -> dict[str, Any]:
    code = _clean_ticker(ticker)
    url = f"https://www.nzx.com/instruments/{code}"
    time.sleep(throttle_s)
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    resp.raise_for_status()
    parsed = _parse_snapshot(resp.text)
    shares = parsed.get("shares_outstanding")
    shares_bn = None if shares is None else shares / 1e9
    return {
        "company": parsed.get("company"),
        "shares_outstanding": shares,
        "shares_bn": shares_bn,
        "company_source": parsed.get("company_source"),
        "shares_source": parsed.get("shares_source"),
        "source_url": url,
    }
