from __future__ import annotations

import pandas as pd


def robust_numeric_clean(df: pd.DataFrame, column_name: str) -> pd.DataFrame:
    """Safely converts a dataframe column to numeric. ALWAYS returns a DataFrame."""
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(df) if df is not None else pd.DataFrame()

    if column_name in df.columns:
        clean_col = (
            df[column_name].astype(str)
            .str.replace(r"[$,%]", "", regex=True)
            .str.replace(",", "")
            .str.strip()
            .replace(["#N/A", "#VALUE!", "#DIV/0!", "None", "nan", "", "-"], "0")
        )
        df[column_name] = pd.to_numeric(clean_col, errors="coerce").fillna(0)

    return df
