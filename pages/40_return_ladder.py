import time

import pandas as pd
import streamlit as st

from src.data.sheets import get_gspread_client
from src.services.market_data import fetch_nz_quote, fetch_us_quote
from src.services.return_ladder_dcf import (
    DCFInputs,
    build_dcf,
    build_summary_row,
    coerce_inputs_df,
    validate_rows,
)


st.set_page_config(page_title="NZ Wealth Manager Pro - Return Ladder", page_icon="🪜", layout="wide")
st.title("🪜 NZ Wealth Manager Pro - Return Ladder")
st.caption("DCF-style return ladders with live market data and per-ticker cashflow tables.")

DEFAULT_REQUIRED_RETURNS = [0.08, 0.10, 0.15, 0.20]


def _parse_required_returns(raw: str) -> list[float]:
    returns = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = float(part)
        except ValueError:
            continue
        if value > 1.0:
            value = value / 100.0
        returns.append(value)
    returns = sorted(set(returns))
    return returns or DEFAULT_REQUIRED_RETURNS


def _parse_price(value: str) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _should_overwrite(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return float(value) == 0.0


@st.cache_data(ttl=3600)
def _load_nzx_fallback_prices() -> dict[str, float]:
    sheet_id = str(st.secrets.get("nzx_quotes_sheet_id", "")).strip()
    tab = str(st.secrets.get("nzx_quotes_tab", "NZX_QUOTES")).strip()
    if not sheet_id:
        return {}

    client = get_gspread_client()
    ws = client.open_by_key(sheet_id).worksheet(tab)
    values = ws.get_all_values()
    if not values:
        return {}

    headers = [str(h).strip() for h in values[0]]
    df = pd.DataFrame(values[1:], columns=headers)
    if df.empty:
        return {}

    ticker_col = next((c for c in headers if c.lower() in {"ticker", "code", "symbol"}), None)
    price_col = next(
        (c for c in headers if c.lower() in {"price", "last", "last price", "last_price", "headline price"}),
        None,
    )
    if not ticker_col or not price_col:
        return {}

    prices = {}
    for _, row in df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip().upper()
        price = _parse_price(row.get(price_col))
        if ticker and price is not None:
            prices[ticker] = price
    return prices


@st.cache_data(ttl=60 * 3060)
def _fetch_quotes(rows: list[dict], nzx_fallback: dict[str, float], refresh_token: float):
    quotes = {}
    warnings = []
    for row in rows:
        ticker = str(row.get("ticker", "")).strip().upper()
        market = str(row.get("market", "")).strip().upper()
        exchange = str(row.get("exchange", "")).strip().upper()
        if not ticker or not market:
            continue
        try:
            if market == "US":
                quote = fetch_us_quote(ticker, exchange=exchange or None)
            elif market == "NZ":
                quote = fetch_nz_quote(ticker, fallback_prices=nzx_fallback)
            else:
                warnings.append(f"{ticker}: unsupported market '{market}'.")
                continue
        except Exception as exc:
            warnings.append(f"{ticker}: quote fetch failed ({exc}).")
            continue

        if quote.price is None:
            warnings.append(f"{ticker}: price not found from {quote.source}.")

        quotes[(ticker, market)] = quote
    return quotes, warnings


def _default_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAPL",
                "market": "US",
                "exchange": "NASDAQ",
                "current_price": 0.0,
                "shares_out": 0.0,
                "net_cash_or_debt": 0.0,
                "fcf_year0": 100000000.0,
                "years_to_exit": 5,
                "exit_multiple": 20.0,
                "growth_rate": 0.05,
                "quote_source": "",
            },
            {
                "ticker": "IFT",
                "market": "NZ",
                "exchange": "NZX",
                "current_price": 0.0,
                "shares_out": 0.0,
                "net_cash_or_debt": 0.0,
                "fcf_year0": 50000000.0,
                "years_to_exit": 5,
                "exit_multiple": 18.0,
                "growth_rate": 0.04,
                "quote_source": "",
            },
        ]
    )


st.sidebar.header("DCF Settings")
required_returns_input = st.sidebar.text_input(
    "Required Returns (comma separated)",
    value=", ".join(str(r) for r in DEFAULT_REQUIRED_RETURNS),
)
required_returns = _parse_required_returns(required_returns_input)
base_return = st.sidebar.selectbox(
    "Base Return for Upside",
    required_returns,
    index=required_returns.index(0.10) if 0.10 in required_returns else 0,
)
zone_green = st.sidebar.number_input("Green Zone Threshold (+%)", value=20.0, step=5.0) / 100
zone_red = st.sidebar.number_input("Red Zone Threshold (-%)", value=-20.0, step=5.0) / 100

st.sidebar.header("Quotes")
refresh = st.sidebar.button("Refresh Quotes")
if refresh:
    st.session_state["quotes_refresh_token"] = time.time()
    st.rerun()

refresh_token = st.session_state.get("quotes_refresh_token", 0.0)

st.subheader("Inputs")
if "return_ladder_rows" not in st.session_state:
    st.session_state["return_ladder_rows"] = _default_rows()

edited = st.data_editor(
    st.session_state["return_ladder_rows"],
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "market": st.column_config.SelectboxColumn("market", options=["US", "NZ"]),
        "current_price": st.column_config.NumberColumn(format="%.2f"),
        "shares_out": st.column_config.NumberColumn(format="%.0f"),
        "net_cash_or_debt": st.column_config.NumberColumn(format="%.0f"),
        "fcf_year0": st.column_config.NumberColumn(format="%.0f"),
        "growth_rate": st.column_config.NumberColumn(format="%.2%"),
        "exit_multiple": st.column_config.NumberColumn(format="%.2f"),
    },
)
edited = coerce_inputs_df(edited)
st.session_state["return_ladder_rows"] = edited

rows = edited.to_dict(orient="records")
nzx_fallback = _load_nzx_fallback_prices()
quotes, quote_warnings = _fetch_quotes(rows, nzx_fallback, refresh_token)

if quote_warnings:
    st.warning("Quote issues:\n" + "\n".join(f"- {w}" for w in quote_warnings))

updated_rows = []
for row in rows:
    ticker = str(row.get("ticker", "")).strip().upper()
    market = str(row.get("market", "")).strip().upper()
    quote = quotes.get((ticker, market))
    if quote:
        row["quote_source"] = quote.source
        if quote.price is not None and _should_overwrite(row.get("current_price")):
            row["current_price"] = quote.price
        if quote.shares_outstanding and _should_overwrite(row.get("shares_out")):
            row["shares_out"] = float(quote.shares_outstanding)
    updated_rows.append(row)

updated_df = coerce_inputs_df(pd.DataFrame(updated_rows))
st.session_state["return_ladder_rows"] = updated_df

summary_rows = []
results = {}
errors = []

row_errors, row_warnings = validate_rows(updated_df.to_dict(orient="records"))
if row_warnings:
    st.warning("Warnings:\n" + "\n".join(f"- {w}" for w in row_warnings))
error_tickers = {msg.split(":", 1)[0] for msg in row_errors}

for row in updated_df.to_dict(orient="records"):
    ticker = str(row.get("ticker", "")).strip().upper()
    market = str(row.get("market", "")).strip().upper()
    if not ticker or not market:
        continue
    if ticker in error_tickers:
        continue

    try:
        inputs = DCFInputs(
            ticker=ticker,
            market=market,
            current_price=float(row.get("current_price") or 0),
            shares_out=float(row.get("shares_out") or 0),
            net_cash=float(row.get("net_cash_or_debt") or 0),
            fcf0=float(row.get("fcf_year0") or 0),
            years=int(row.get("years_to_exit") or 0),
            exit_multiple=float(row.get("exit_multiple") or 0),
            growth_rate=float(row.get("growth_rate") or 0),
        )
        if inputs.years <= 0:
            errors.append(f"{ticker}: years_to_exit must be greater than 0.")
            continue
        result = build_dcf(inputs, required_returns)
        results[ticker] = result
        summary_rows.append(build_summary_row(inputs, result, base_return, zone_green, zone_red))
    except Exception as exc:
        errors.append(f"{ticker}: {exc}")

if row_errors or errors:
    st.error(
        "Input errors:\n"
        + "\n".join(f"- {e}" for e in (row_errors + errors))
    )

st.subheader("Summary")
if summary_rows:
    summary_df = pd.DataFrame(summary_rows)
    fv_cols = [c for c in summary_df.columns if c.startswith("FV@")]
    display_cols = ["Ticker", "Market", "Current Price"] + fv_cols + ["Upside @ Base", "Zone"]
    summary_df = summary_df[display_cols]
    st.dataframe(
        summary_df.style.format(
            {**{c: "${:,.2f}" for c in fv_cols}, "Current Price": "${:,.2f}", "Upside @ Base": "{:+.1%}"},
            na_rep="N/A",
        ),
        use_container_width=True,
    )
else:
    st.info("Add tickers and refresh quotes to build the summary table.")

st.subheader("DCF Blocks")
for ticker, result in results.items():
    inputs = result.inputs
    with st.expander(f"{ticker} ({inputs.market}) DCF"):
        if inputs.fcf0 < 0:
            st.warning("FCF is negative; PVs and fair values will reflect cash burn.")

        st.dataframe(result.pv_table.style.format("{:,.0f}"), use_container_width=True)

        metric_rows = []
        for required_return in required_returns:
            metric_rows.append(
                {
                    "Required Return": f"{required_return:.0%}",
                    "Enterprise Value": result.enterprise_values[required_return],
                    "Equity Value": result.equity_values[required_return],
                    "Fair Value / Share": result.fair_values[required_return],
                }
            )

        metrics_df = pd.DataFrame(metric_rows)
        st.dataframe(
            metrics_df.style.format(
                {
                    "Enterprise Value": "${:,.0f}",
                    "Equity Value": "${:,.0f}",
                    "Fair Value / Share": "${:,.2f}",
                },
                na_rep="N/A",
            ),
            use_container_width=True,
        )

        st.caption(
            f"Exit multiple: {inputs.exit_multiple:.1f}x | Growth: {inputs.growth_rate:.1%} | Net cash/debt: {inputs.net_cash:,.0f}"
        )
