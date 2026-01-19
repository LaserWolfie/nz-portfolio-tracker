import pandas as pd
import unicodedata
import re


def clean_number(value, *, default=0.0, nan_on_invalid=False):
    """
    Normalizes numeric strings like "$1,234" to float.
    Use nan_on_invalid=True when downstream logic expects NaN.
    """
    if pd.isna(value) or str(value).strip() in ["", "-", "None", "nan", "N/A"]:
        return float("nan") if nan_on_invalid else default
    s = str(value).upper().replace(",", "").replace("$", "").replace(" ", "").replace("%", "")
    try:
        return float(s)
    except Exception:
        return float("nan") if nan_on_invalid else default


def clean_percent(value, *, default=0.0):
    """
    Normalizes percent strings; if value > 2, treat as whole percent.
    """
    if pd.isna(value) or str(value).strip() in ["", "-", "None", "nan", "N/A"]:
        return default
    s = str(value).replace("%", "").replace(",", "").strip()
    try:
        val = float(s)
        return val / 100.0 if val > 2.0 else val
    except Exception:
        return default


def normalize_entity_name(value) -> str:
    if pd.isna(value):
        return ""
    s = unicodedata.normalize("NFKC", str(value))
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)
    s = s.replace("\u00A0", " ")
    s = " ".join(s.split())
    s = s.strip()
    return s.casefold()


def debug_repr(s: str) -> str:
    return repr(s)


def robust_numeric_clean(df, column_name):
    """Safely converts a spreadsheet column to numbers, handling symbols and errors."""
    df = df.copy()
    if column_name in df.columns:
        clean_col = (
            df[column_name].astype(str)
            .str.replace(r"[$,%]", "", regex=True)
            .str.replace(",", "")
            .str.strip()
            .replace(["#N/A", "#VALUE!", "#DIV/0!", "None", "nan", "", "-"], "0")
        )
        df.loc[:, column_name] = pd.to_numeric(clean_col, errors="coerce").fillna(0)
    return df
