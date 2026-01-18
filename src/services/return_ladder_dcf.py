from dataclasses import dataclass
import math
from typing import Dict, List, Tuple

import pandas as pd


@dataclass(frozen=True)
class DCFInputs:
    ticker: str
    market: str
    current_price: float
    shares_out: float
    net_cash: float
    fcf1: float
    years: int
    exit_multiple: float
    growth_rate: float
    terminal_growth_rate: float


@dataclass(frozen=True)
class DCFResult:
    inputs: DCFInputs
    pv_table: pd.DataFrame
    fair_values: Dict[float, float]
    enterprise_values: Dict[float, float]
    equity_values: Dict[float, float]


def coerce_inputs_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    numeric_cols = [
        "shares_out",
        "fcf_year0",
        "fcf1",
        "years_to_exit",
        "exit_multiple",
        "growth_rate",
        "g_5y",
        "g_terminal",
        "current_price",
        "net_cash_or_debt",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "years_to_exit" in df.columns:
        df["years_to_exit"] = df["years_to_exit"].fillna(5).astype(int)
    if "exit_multiple" in df.columns:
        df["exit_multiple"] = df["exit_multiple"].fillna(20.0)
    if "growth_rate" in df.columns:
        df["growth_rate"] = df["growth_rate"].fillna(0.0)
    if "g_5y" in df.columns:
        df["g_5y"] = df["g_5y"].fillna(0.0)
    if "g_terminal" in df.columns:
        df["g_terminal"] = df["g_terminal"].fillna(0.0)
    if "fcf_year0" in df.columns:
        df["fcf_year0"] = df["fcf_year0"].fillna(0.0)
    if "fcf1" in df.columns:
        df["fcf1"] = df["fcf1"].fillna(0.0)
    if "net_cash_or_debt" in df.columns:
        df["net_cash_or_debt"] = df["net_cash_or_debt"].fillna(0.0)
    if "current_price" in df.columns:
        df["current_price"] = df["current_price"].fillna(0.0)
    if "shares_out" in df.columns:
        df["shares_out"] = df["shares_out"].fillna(0.0)

    return df


def validate_rows(rows: List[dict]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    for row in rows:
        ticker = str(row.get("ticker", "")).strip().upper() or "Unknown"
        missing = []
        years = row.get("years_to_exit")
        exit_multiple = row.get("exit_multiple")
        growth_rate = row.get("g_5y")
        if growth_rate is None or (isinstance(growth_rate, float) and math.isnan(growth_rate)):
            growth_rate = row.get("growth_rate")
        fcf1 = row.get("fcf1")
        fcf_year0 = row.get("fcf_year0")
        shares_out = row.get("shares_out")

        if years is None or years <= 0:
            missing.append("years_to_exit")
        # exit_multiple is optional when using perpetuity-growth terminal value
        if growth_rate is None or (isinstance(growth_rate, float) and math.isnan(growth_rate)):
            missing.append("growth_rate")
        if fcf1 is None or (isinstance(fcf1, float) and math.isnan(fcf1)):
            if fcf_year0 is None or (isinstance(fcf_year0, float) and math.isnan(fcf_year0)):
                missing.append("fcf1")
            else:
                warnings.append(f"{ticker}: fcf1 missing - trailing FCF will be used as FCF1")

        if missing:
            errors.append(f"{ticker}: missing/invalid {', '.join(missing)}")

        if shares_out is None or shares_out <= 0:
            warnings.append(f"{ticker}: shares_out missing - FV/share will show N/A")

    return errors, warnings


def build_dcf(inputs: DCFInputs, required_returns: List[float]) -> DCFResult:
    if inputs.years < 1:
        raise ValueError("years must be at least 1")

    years = list(range(1, inputs.years + 1))
    fcfs = [inputs.fcf1 * ((1.0 + inputs.growth_rate) ** (year - 1)) for year in years]

    table = pd.DataFrame({"Year": years, "FCF": fcfs})

    fair_values: Dict[float, float] = {}
    enterprise_values: Dict[float, float] = {}
    equity_values: Dict[float, float] = {}

    for required_return in required_returns:
        terminal_fcf = fcfs[-1] * (1.0 + inputs.terminal_growth_rate)
        if required_return <= inputs.terminal_growth_rate:
            raise ValueError("required_return must be greater than g_terminal")
        terminal_value = terminal_fcf / (required_return - inputs.terminal_growth_rate)
        pv_column = []
        for year, fcf in zip(years, fcfs):
            pv = fcf / ((1.0 + required_return) ** year)
            pv_column.append(pv)
        table[f"PV@{required_return:.0%}"] = pv_column

        pv_terminal = terminal_value / ((1.0 + required_return) ** inputs.years)
        enterprise_value = sum(pv_column) + pv_terminal
        equity_value = enterprise_value + inputs.net_cash
        fair_value = math.nan
        if inputs.shares_out > 0:
            fair_value = equity_value / inputs.shares_out

        fair_values[required_return] = fair_value
        enterprise_values[required_return] = enterprise_value
        equity_values[required_return] = equity_value

    table = table.set_index("Year")

    return DCFResult(
        inputs=inputs,
        pv_table=table,
        fair_values=fair_values,
        enterprise_values=enterprise_values,
        equity_values=equity_values,
    )


def build_summary_row(
    inputs: DCFInputs,
    result: DCFResult,
    base_return: float,
    zone_green: float,
    zone_red: float,
) -> Dict[str, float | str]:
    base_fv = result.fair_values.get(base_return)
    upside = None
    if base_fv is not None and not math.isnan(base_fv) and inputs.current_price:
        upside = (base_fv - inputs.current_price) / inputs.current_price

    zone = "Neutral"
    if upside is not None:
        if upside >= zone_green:
            zone = "Green"
        elif upside <= zone_red:
            zone = "Red"

    summary = {
        "Ticker": inputs.ticker,
        "Market": inputs.market,
        "Current Price": inputs.current_price,
        "Upside @ Base": upside,
        "Zone": zone,
    }
    for required_return, fv in result.fair_values.items():
        summary[f"FV@{required_return:.0%}"] = fv
    return summary
