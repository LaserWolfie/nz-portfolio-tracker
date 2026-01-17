import streamlit as st

from src.services.return_ladder import LadderInputs, build_ladder

st.set_page_config(page_title="Return Ladder", page_icon="🪜", layout="wide")
st.title("🪜 Return Ladder")
st.caption("Estimate implied share prices from FCF growth and exit multiple scenarios.")

st.sidebar.header("Ladder Inputs")
ticker = st.sidebar.text_input("Ticker", value="ABC")
market = st.sidebar.text_input("Market", value="NZ")
price = st.sidebar.number_input("Current Price", min_value=0.0, value=10.0, step=0.1, format="%.2f")
shares_out = st.sidebar.number_input(
    "Shares Outstanding",
    min_value=0.0,
    value=100000000.0,
    step=1000000.0,
    format="%.0f",
)
net_cash = st.sidebar.number_input(
    "Net Cash (Debt)",
    value=0.0,
    step=1000000.0,
    format="%.0f",
)
fcf0 = st.sidebar.number_input(
    "FCF (Year 0)",
    value=10000000.0,
    step=1000000.0,
    format="%.0f",
)
years = st.sidebar.number_input(
    "Years to Exit",
    min_value=3,
    max_value=10,
    value=5,
    step=1,
)

st.sidebar.subheader("FCF Growth Scenarios (%)")
bear_growth = st.sidebar.number_input("Bear Growth", value=-2.0, step=0.5, format="%.1f") / 100
base_growth = st.sidebar.number_input("Base Growth", value=5.0, step=0.5, format="%.1f") / 100
bull_growth = st.sidebar.number_input("Bull Growth", value=10.0, step=0.5, format="%.1f") / 100

st.sidebar.subheader("Exit Multiples (x FCF)")
low_multiple = st.sidebar.number_input("Low Multiple", value=10.0, step=0.5, format="%.1f")
mid_multiple = st.sidebar.number_input("Mid Multiple", value=15.0, step=0.5, format="%.1f")
high_multiple = st.sidebar.number_input("High Multiple", value=20.0, step=0.5, format="%.1f")

if shares_out <= 0:
    st.error("Shares outstanding must be greater than 0.")
    st.stop()

if years < 3 or years > 10:
    st.error("Years to exit must be between 3 and 10.")
    st.stop()

if fcf0 < 0:
    st.warning("FCF is negative; implied values will reflect ongoing cash burn.")

inputs = LadderInputs(
    ticker=ticker.strip(),
    market=market.strip(),
    price=price,
    shares_out=shares_out,
    net_cash=net_cash,
    fcf0=fcf0,
    years=int(years),
)

growth_rates = {
    "Bear": bear_growth,
    "Base": base_growth,
    "Bull": bull_growth,
}
exit_multiples = {
    "Low": low_multiple,
    "Mid": mid_multiple,
    "High": high_multiple,
}

ladder = build_ladder(inputs, growth_rates, exit_multiples)

st.subheader(f"{inputs.ticker} {inputs.market} Return Ladder")
st.metric("Current Price", f"${inputs.price:,.2f}")
st.dataframe(ladder.style.format("${:,.2f}"), use_container_width=True)
