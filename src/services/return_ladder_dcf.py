from dataclasses import dataclass
from typing import Dict, List

import pandas as pd


@dataclass(frozen=True)
class DCFInputs:
    ticker: str
    market: str
    current_price: float
    shares_out: float
    net_cash: float
    fcf0: float
    years: int
    exit_multiple: float
    growth_rate: float


@dataclass(frozen=True)
class DCFResult:
    inputs: DCFInputs
    pv_table: pd.DataFrame
    fair_values: Dict[float, float]
    enterprise_values: Dict[float, float]
    equity_values: Dict[float, float]


def build_dcf(inputs: DCFInputs, required_returns: List[float]) -> DCFResult:
    if inputs.shares_out <= 0:
        raise ValueError("shares_out must be greater than 0")
    if inputs.years < 1:
        raise ValueError("years must be at least 1")

    years = list(range(1, inputs.years + 1))
    fcfs = [inputs.fcf0 * ((1.0 + inputs.growth_rate) ** year) for year in years]

    table = pd.DataFrame({"Year": years, "FCF": fcfs})

    fair_values: Dict[float, float] = {}
    enterprise_values: Dict[float, float] = {}
    equity_values: Dict[float, float] = {}

    terminal_fcf = fcfs[-1]
    terminal_value = terminal_fcf * inputs.exit_multiple

    for required_return in required_returns:
        pv_column = []
        for year, fcf in zip(years, fcfs):
            pv = fcf / ((1.0 + required_return) ** year)
            pv_column.append(pv)
        table[f"PV@{required_return:.0%}"] = pv_column

        pv_terminal = terminal_value / ((1.0 + required_return) ** inputs.years)
        enterprise_value = sum(pv_column) + pv_terminal
        equity_value = enterprise_value + inputs.net_cash
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
    if base_fv is not None and inputs.current_price:
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
