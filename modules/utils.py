import pandas as pd


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
