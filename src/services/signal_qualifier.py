from __future__ import annotations

from typing import Dict, Iterable, Set

import pandas as pd


def _norm(s: str) -> str:
    return str(s or "").strip().upper()


def build_alias_map(stock_df: pd.DataFrame) -> Dict[str, Set[str]]:
    """
    Build alias sets per ticker: ticker, exchange forms, and company name if present.
    """
    if not isinstance(stock_df, pd.DataFrame) or stock_df.empty:
        return {}

    name_cols = [c for c in ["Company", "Company Name", "Name", "Security", "Instrument"] if c in stock_df.columns]
    alias_map: Dict[str, Set[str]] = {}

    for _, row in stock_df.iterrows():
        ticker = _norm(row.get("Ticker", ""))
        if not ticker:
            continue

        aliases = {ticker, f"NZX: {ticker}", f"ASX: {ticker}", f"{ticker}.NZ", f"{ticker}.AX"}
        for col in name_cols:
            name = str(row.get(col, "")).strip()
            if name:
                aliases.add(name)

        alias_map.setdefault(ticker, set()).update(aliases)

    return alias_map


def qualifies_signal(row: pd.Series, ticker: str, aliases: Iterable[str]) -> bool:
    """
    Return True if title/summary/source text contains a strong alias.
    If ticker length <= 3, do not allow the bare ticker to qualify.
    """
    t_norm = _norm(ticker)
    if not t_norm:
        return False

    text = " ".join(
        [
            str(row.get("Title", "")),
            str(row.get("Summary", "")),
            str(row.get("Source", "")),
        ]
    ).lower()

    alias_set = {str(a).strip() for a in aliases if str(a).strip()}
    if len(t_norm) <= 3:
        alias_set = {a for a in alias_set if _norm(a) != t_norm}

    for alias in alias_set:
        if alias.lower() in text:
            return True

    return False


def apply_qualifies(signals_df: pd.DataFrame, alias_map: Dict[str, Set[str]]) -> pd.DataFrame:
    """
    Add QualifiesAuto boolean column using alias_map.
    """
    if not isinstance(signals_df, pd.DataFrame):
        return pd.DataFrame()
    if signals_df.empty:
        out = signals_df.copy()
        out["QualifiesAuto"] = False
        return out

    df = signals_df.copy()

    def _row_qualifies(r: pd.Series) -> bool:
        ticker = _norm(r.get("Ticker", ""))
        if not ticker:
            return False
        aliases = alias_map.get(ticker, {ticker})
        return qualifies_signal(r, ticker, aliases)

    df["QualifiesAuto"] = df.apply(_row_qualifies, axis=1)
    return df
