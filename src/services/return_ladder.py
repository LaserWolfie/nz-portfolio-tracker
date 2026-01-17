from dataclasses import dataclass
from typing import Dict

import pandas as pd


@dataclass(frozen=True)
class LadderInputs:
    ticker: str
    market: str
    price: float
    shares_out: float
    net_cash: float
    fcf0: float
    years: int


def build_ladder(
    inputs: LadderInputs,
    growth_rates: Dict[str, float],
    exit_multiples: Dict[str, float],
) -> pd.DataFrame:
    if inputs.shares_out <= 0:
        raise ValueError("shares_out must be greater than 0")
    if inputs.years < 3 or inputs.years > 10:
        raise ValueError("years must be between 3 and 10")

    rows = []
    for growth_label, growth_rate in growth_rates.items():
        terminal_fcf = inputs.fcf0 * ((1.0 + growth_rate) ** inputs.years)
        row = {}
        for multiple_label, multiple in exit_multiples.items():
            enterprise_value = terminal_fcf * multiple
            equity_value = enterprise_value + inputs.net_cash
            row[multiple_label] = equity_value / inputs.shares_out
        rows.append(row)

    ladder = pd.DataFrame(rows, index=list(growth_rates.keys()))
    ladder.index.name = "FCF growth"
    return ladder
