import pandas as pd


def robust_numeric_clean(df, column_name):
    cleaned = (
        df[column_name]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace(
            ["#N/A", "#VALUE!", "#DIV/0!", "None", "nan", "", "-"],
            "0",
        )
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)
