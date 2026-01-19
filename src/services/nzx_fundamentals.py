from __future__ import annotations

import re
import time

import requests


def _clean_ticker(ticker: str) -> str:
    text = str(ticker or "").strip().upper()
    if text.startswith("NZX:"):
        return text.split("NZX:", 1)[1]
    if text.startswith("NZ:"):
        return text.split("NZ:", 1)[1]
    return text


def _parse_securities_issued(html: str) -> int | None:
    match = re.search(r"Securities Issued.*?([0-9][0-9,]*)", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    raw = match.group(1)
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return None


def fetch_nzx_securities_issued(ticker: str, throttle_s: float = 0.3) -> tuple[int | None, str]:
    code = _clean_ticker(ticker)
    url = f"https://www.nzx.com/instruments/{code}"
    time.sleep(throttle_s)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    issued = _parse_securities_issued(resp.text)
    return issued, url
