from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import re
import pandas as pd

_BAD_TICKERS = {"", "TOTAL", "INCLUDE", "NONE", "N/A"}


def normalize_ticker(t):
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return None
    s = str(t).strip().upper()

    # common non-tickers
    if s in _BAD_TICKERS:
        return None

    # drop private buckets from matching
    if s.startswith("PRIVATE:"):
        return None

    # if it has an exchange prefix, keep the part AFTER the colon
    # e.g. NZE:EBO -> EBO, NZ:EBO -> EBO, ASX:KSN -> KSN
    if ":" in s:
        s = s.split(":")[-1].strip()

    # strip common exchange suffixes
    s = re.sub(r"\.(NZ|AX|AS)$", "", s)

    # final clean
    s = re.sub(r"[^A-Z0-9._-]", "", s).strip()
    return s or None


def filter_signals_for_tickers(
    signals_df: Optional[pd.DataFrame],
    tickers: Iterable[str],
    days: int = 14,
) -> pd.DataFrame:
    """
    Filter signals to portfolio tickers and recent dates.

    - Returns empty DataFrame if signals_df is None/empty.
    - Parses Published using pandas to_datetime (errors='coerce').
    - Filters to supplied tickers (normalized) and last N days if dates are present.
    - Sorts newest first.
    """
    if signals_df is None:
        return pd.DataFrame()
    if not isinstance(signals_df, pd.DataFrame) or signals_df.empty:
        return pd.DataFrame()

    df = signals_df.copy()

    portfolio_set = {normalize_ticker(x) for x in tickers}
    portfolio_set = {x for x in portfolio_set if x}

    if "Ticker" in df.columns:
        df["TickerNorm"] = df["Ticker"].apply(normalize_ticker)

    if portfolio_set and "TickerNorm" in df.columns:
        df = df[df["TickerNorm"].isin(portfolio_set)].copy()

    if df.empty:
        if "Ticker" in signals_df.columns:
            signals_raw = sorted(list(signals_df["Ticker"].dropna().unique()))[:30]
            signals_norm = (
                sorted(list(signals_df["Ticker"].apply(normalize_ticker).dropna().unique()))[:30]
            )
            portfolio_raw = sorted([str(t) for t in tickers if str(t).strip()])[:30]
            portfolio_norm = sorted(list(portfolio_set))[:30]
            intersection = set(signals_norm).intersection(set(portfolio_norm))
            print("Market context debug: signals_raw", signals_raw)
            print("Market context debug: signals_norm", signals_norm)
            print("Market context debug: portfolio_raw", portfolio_raw)
            print("Market context debug: portfolio_norm", portfolio_norm)
            print("Market context debug: intersection_size", len(intersection))
        return df

    if "Published" in df.columns:
        df["_published_dt"] = pd.to_datetime(df["Published"], errors="coerce", utc=True)
        if df["_published_dt"].notna().any():
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            df = df[df["_published_dt"].notna()].copy()
            df = df[df["_published_dt"] >= pd.Timestamp(cutoff)].copy()

    if "_published_dt" in df.columns:
        df = df.sort_values("_published_dt", ascending=False)

    return df


def build_market_context_markdown(filtered_df: pd.DataFrame, max_items: int = 6) -> str:
    """
    Build a markdown bullet list for market context.

    One line per signal:
      **TICKER** (FeedType): [Title](URL)  PublishedDate
    Newest first, deduped by URL where possible.
    """
    if not isinstance(filtered_df, pd.DataFrame) or filtered_df.empty:
        return "_No signals found._"

    df = filtered_df.copy()

    if "_published_dt" not in df.columns:
        if "Published" in df.columns:
            df["_published_dt"] = pd.to_datetime(df["Published"], errors="coerce", utc=True)
        else:
            df["_published_dt"] = pd.NaT

    df["_ticker_norm"] = df.get("Ticker", "").astype(str).map(normalize_ticker)
    df = df.sort_values("_published_dt", ascending=False)

    if "URL" in df.columns:
        df["_url_norm"] = df["URL"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["_url_norm"])

    if "Title" in df.columns:
        df["_title_norm"] = df["Title"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["_ticker_norm", "_title_norm"])

    selected_indices = []
    seen_tickers = set()
    for idx, row in df.iterrows():
        t_norm = row.get("_ticker_norm", "")
        if t_norm and t_norm not in seen_tickers:
            selected_indices.append(idx)
            seen_tickers.add(t_norm)
            if len(selected_indices) >= max_items:
                break

    if len(selected_indices) < max_items:
        for idx in df.index:
            if idx in selected_indices:
                continue
            selected_indices.append(idx)
            if len(selected_indices) >= max_items:
                break

    lines = []
    for idx in selected_indices:
        row = df.loc[idx]
        ticker = normalize_ticker(str(row.get("Ticker", "")).strip()) or ""
        feed = str(row.get("FeedType", "")).strip()
        title = str(row.get("Title", "")).strip()
        url = str(row.get("URL", "")).strip()
        published = ""
        if pd.notna(row.get("_published_dt")):
            published = row.get("_published_dt").strftime("%Y-%m-%d")

        title_md = f"[{title}]({url})" if url else title
        feed_md = f" ({feed})" if feed else ""
        published_md = f"  {published}" if published else ""
        lines.append(f"- **{ticker}**{feed_md}: {title_md}{published_md}")

    return "\n".join(lines)
