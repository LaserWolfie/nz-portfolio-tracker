import streamlit as st
import yfinance as yf
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="NZ Portfolio Analyzer", page_icon="🥝", layout="wide")
st.title("🥝 NZ Portfolio Analyzer")

# --- CONFIGURATION ---
SHEET_NAME = "Share Portfolio" 
HISTORY_TAB_NAME = "History"
BENCHMARK_TICKER = "^NZ50"  # S&P/NZX 50 Index

# --- CONNECT TO GOOGLE SHEETS (CLOUD READY) ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 1. Try to get secrets from Streamlit Cloud
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    # 2. Fallback to local file (for running on your laptop)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    
    # Open Spreadsheet
    spreadsheet = client.open(SHEET_NAME)
    sheet = spreadsheet.worksheet("Share Portfolio")
    
    # Check History Tab
    try:
        history_sheet = spreadsheet.worksheet(HISTORY_TAB_NAME)
    except:
        st.error(f"⚠️ Could not find a tab named '{HISTORY_TAB_NAME}'. Please create it with columns 'Date' and 'Value'.")
        st.stop()
    
    # --- DATA LOADING & CLEANING ---
    all_values = sheet.get_all_values()
    raw_headers = all_values[0]
    cleaned_headers = [str(h).strip() for h in raw_headers]
    df = pd.DataFrame(all_values[1:], columns=cleaned_headers)
    
    required_cols = ['Ticker', 'Shares', 'Purchase Price']
    for col in required_cols:
        if col not in df.columns:
            st.error(f"❌ Missing column: '{col}'. Check your sheet headers.")
            st.stop()
            
    portfolio = df[required_cols].copy()
    portfolio = portfolio[portfolio['Ticker'] != '']
    
    def clean_currency(x):
        if isinstance(x, str):
            return x.replace('$', '').replace(',', '').strip()
        return x

    portfolio['Shares'] = pd.to_numeric(portfolio['Shares'].apply(clean_currency), errors='coerce')
    portfolio['Purchase Price'] = pd.to_numeric(portfolio['Purchase Price'].apply(clean_currency), errors='coerce')
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price']) 

    def fix_ticker(ticker):
        ticker = str(ticker).strip().upper()
        if ":" in ticker:
            clean_code = ticker.split(":")[-1]
            if "ASX" in ticker: return clean_code + ".AX"
            else: return clean_code + ".NZ"
        return ticker

    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)

    st.sidebar.success("✅ Sync Successful!")
    
    # Sidebar Mini-Chart
    hist_data = history_sheet.get_all_values()
    if len(hist_data) > 1:
        hist_df = pd.DataFrame(hist_data[1:], columns=hist_data[0])
        hist_df['Date'] = pd.to_datetime(hist_df['Date'])
        hist_df['Value'] = pd.to_numeric(hist_df['Value'])
        st.sidebar.subheader("📈 Wealth Trend")
        st.sidebar.line_chart(hist_df.set_index('Date')['Value'])

except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.info("If deploying to Cloud, ensure you added your secrets in Advanced Settings.")
    st.stop()

# --- MAIN DASHBOARD ---
if st.button("Run Full Analysis", type="primary"):
    
    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    
    # --- STEP 1: BENCHMARK & RISK ---
    market_return_pct = 0.0
    market_hist_data = None
    
    with st.spinner('Fetching Benchmark & Calculating Risk...'):
        try:
            market_data = yf.download(BENCHMARK_TICKER, period="1y")
            if 'Close' in market_data.columns: 
                market_hist_data = market_data['Close']
            else: 
                market_hist_data = market_data
            
            if isinstance(market_hist_data, pd.DataFrame): 
                market_hist_data = market_hist_data.iloc[:, 0]

            if len(market_hist_data) >= 2:
                market_now = float(market_hist_data.iloc[-1])
                market_prev = float(market_hist_data.iloc[-2])
                market_return_pct = ((market_now - market_prev) / market_prev) * 100
        except: pass

    # --- STEP 2: PRICE HISTORY & BETA ---
    with st.spinner('Fetching portfolio prices...'):
        try:
            data = yf.download(ticker_list, period="1y")
            if 'Close' in data.columns: close_data = data['Close']
            else: close_data = data
            
            curr_prices, prev_prices, p30_prices, p1y_prices, betas = [], [], [], [], []
            
            if market_hist_data is not None:
                market_returns = market_hist_data.pct_change().dropna()
            else:
                market_returns = pd.Series([])

            for t in ticker_list:
                try:
                    if isinstance(close_data, pd.DataFrame) and t in close_data.columns: s = close_data[t].dropna()
                    elif isinstance(close_data, pd.Series): s = close_data.dropna()
                    else: s = pd.Series([])

                    curr = float(s.iloc[-1]) if len(s)>0 else 0.0
                    prev = float(s.iloc[-2]) if len(s)>=2 else curr
                    p30 = float(s.iloc[-22]) if len(s)>=22 else (float(s.iloc[0]) if len(s)>0 else curr)
                    p1y = float(s.iloc[0]) if len(s)>0 else curr

                    curr_prices.append(curr); prev_prices.append(prev)
                    p30_prices.append(p30); p1y_prices.append(p1y)
                    
                    # Beta Calc
                    if len(s) > 30 and len(market_returns) > 30:
                        stock_returns = s.pct_change().dropna()
                        aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
                        if len(aligned) > 10:
                            cov = aligned.cov().iloc[0, 1]
                            var = aligned.iloc[:, 1].var()
                            betas.append(cov / var)
                        else: betas.append(1.0)
                    else: betas.append(1.0)
                except:
                    curr_prices.append(0.0); prev_prices.append(0.0)
                    p30_prices.append(0.0); p1y_prices.append(0.0); betas.append(1.0)

            portfolio['Current Price'] = curr_prices
            portfolio['Previous Price'] = prev_prices
            portfolio['Price 30d'] = p30_prices
            portfolio['Price 1y'] = p1y_prices
            portfolio['Beta'] = betas
            
        except Exception as e:
            st.error(f"Price Error: {e}"); st.stop()

    # --- STEP 3: FUNDAMENTALS (HYBRID MODE) ---
    progress = st.progress(0); status = st.empty()
    pe_ratios, div_yields = [], []
    
    # We now read 'Sector' directly from the DataFrame we loaded from Google Sheets
    # Check if 'Sector' exists in the sheet, otherwise default to "Unknown"
    if 'Sector' in portfolio.columns:
        sectors = portfolio['Sector'].tolist()
    else:
        sectors = ["Unknown"] * len(ticker_list)
    
    for i, t in enumerate(ticker_list):
        status.text(f"Analyzing fundamentals for: {t}")
        progress.progress((i+1)/len(ticker_list))
        try:
            # We ONLY ask Yahoo for P/E and Yield now (saves time!)
            info = yf.Ticker(t).info
            
            pe = info.get('trailingPE', None)
            div = info.get('dividendYield', 0)
            if div is None: div = 0
            div_pct = div * 100 if (div > 0 and div < 0.30) else div
            
            pe_ratios.append(pe); div_yields.append(div_pct)
        except:
            pe_ratios.append(None); div_yields.append(0)
            
    status.empty(); progress.empty()
    portfolio['P/E Ratio'] = pe_ratios
    portfolio['Div Yield %'] = div_yields
    portfolio['Sector'] = sectors # <--- Uses the column from Google Sheets

    # --- CALCULATIONS ---
    portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
    portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
    portfolio['Total Gain $'] = portfolio['Market Value'] - portfolio['Cost Basis']
    portfolio['Total Gain %'] = (portfolio['Total Gain $'] / portfolio['Cost Basis']) * 100
    portfolio['Day Change $'] = (portfolio['Current Price'] - portfolio['Previous Price']) * portfolio['Shares']
    portfolio['Day Change %'] = ((portfolio['Current Price'] - portfolio['Previous Price']) / portfolio['Previous Price']) * 100
    portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
    portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100
    portfolio['Est. Annual Income'] = portfolio['Market Value'] * (portfolio['Div Yield %'] / 100)

    # --- METRICS ---
    total_value = portfolio['Market Value'].sum()
    total_cost = portfolio['Cost Basis'].sum()
    total_profit_val = total_value - total_cost
    total_profit_pct = (total_profit_val / total_cost) * 100 if total_cost > 0 else 0
    day_gain_val = portfolio['Day Change $'].sum()
    est_income = portfolio['Est. Annual Income'].sum()
    yield_on_market = (est_income / total_value) * 100 if total_value > 0 else 0
    
    # Portfolio Beta
    if total_value > 0:
        portfolio['Weight'] = portfolio['Market Value'] / total_value
        portfolio_beta = (portfolio['Weight'] * portfolio['Beta']).sum()
    else: portfolio_beta = 1.0

    if portfolio_beta > 1.15:
        risk_label = "Aggressive 🚀"; risk_msg = "Higher volatility than market. Targeting growth."
    elif portfolio_beta < 0.85:
        risk_label = "Defensive 🛡️"; risk_msg = "Lower volatility. Preservation focus."
    else:
        risk_label = "Balanced ⚖️"; risk_msg = "Tracking market volatility."

    # --- SAVE HISTORY ---
    today_str = datetime.now().strftime("%Y-%m-%d")
    existing_history = history_sheet.get_all_values()
    if len(existing_history) < 2 or existing_history[-1][0] != today_str:
        history_sheet.append_row([today_str, total_value])
        st.toast(f"✅ Saved today's value: ${total_value:,.2f}")
    else: st.toast("ℹ️ History already up to date.")

    # --- DISPLAY ROW 1 ---
    st.subheader("📊 Portfolio Health")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Value", f"${total_value:,.2f}")
    c2.metric("Total Profit", f"${total_profit_val:,.2f}", f"{total_profit_pct:.2f}%")
    c3.metric("Today's Gain", f"${day_gain_val:,.2f}")
    c4.metric("Est. Dividends/Yr", f"${est_income:,.2f}", f"{yield_on_market:.2f}% Yield")

    # --- DISPLAY ROW 2 (RISK) ---
    st.markdown("---")
    st.subheader("🧠 Risk & Benchmark")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("NZX 50 Today", f"{market_return_pct:+.2f}%")
    
    prev_val = total_value - day_gain_val
    port_day_pct = (day_gain_val / prev_val * 100) if prev_val > 0 else 0
    alpha = port_day_pct - market_return_pct
    b2.metric("Alpha (vs Market)", f"{alpha:+.2f}%", delta=f"Portfolio: {port_day_pct:+.2f}%")
    b3.metric("Portfolio Beta", f"{portfolio_beta:.2f}", help=">1.0 = High Risk")
    b4.metric("Strategy", risk_label)
    st.caption(f"Risk Assessment: {risk_msg}")

    # --- CHARTS ---
    st.markdown("---")
    tab1, tab2 = st.tabs(["🔎 Holdings Table", "📈 Wealth History"])
    
    with tab1:
        col_table, col_pie = st.columns([2.5, 1])
        with col_table:
            display_df = portfolio[['Ticker', 'Sector', 'Current Price', 'Beta', 'Day Change %', '30D %', '1Y %', 'Total Gain %', 'Market Value']].copy()
            display_df = display_df.sort_values(by='Total Gain %', ascending=False)
            st.dataframe(
                display_df.style.format({
                    "Current Price": "${:.2f}", "Market Value": "${:,.0f}",
                    "Day Change %": "{:+.2f}%", "30D %": "{:+.2f}%", 
                    "1Y %": "{:+.2f}%", "Total Gain %": "{:+.2f}%", "Beta": "{:.2f}"
                })
                .background_gradient(subset=['Total Gain %'], cmap="RdYlGn", vmin=-50, vmax=50)
                .background_gradient(subset=['Day Change %'], cmap="RdYlGn", vmin=-5, vmax=5)
                .background_gradient(subset=['30D %'], cmap="RdYlGn", vmin=-10, vmax=10)
                .background_gradient(subset=['1Y %'], cmap="RdYlGn", vmin=-30, vmax=30)
                .background_gradient(subset=['Beta'], cmap="coolwarm", vmin=0.5, vmax=1.5),
                use_container_width=True, height=600
            )
        with col_pie:
            sector_group = portfolio.groupby('Sector')['Market Value'].sum()
            fig, ax = plt.subplots(figsize=(5, 5))
            fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
            colors = plt.cm.Paired(np.linspace(0, 1, len(sector_group)))
            ax.pie(sector_group, labels=sector_group.index, autopct='%1.0f%%', pctdistance=0.8, startangle=90, colors=colors, textprops={'color':"white"})
            fig.gca().add_artist(plt.Circle((0,0),0.60,fc='#0E1117'))
            st.pyplot(fig)

    with tab2:
        fresh_hist = history_sheet.get_all_values()
        if len(fresh_hist) > 1:
            h_df = pd.DataFrame(fresh_hist[1:], columns=fresh_hist[0])
            h_df['Date'] = pd.to_datetime(h_df['Date'])
            h_df['Value'] = pd.to_numeric(h_df['Value'])
            st.subheader("Your Net Worth Journey")
            st.area_chart(h_df.set_index('Date')['Value'], color="#00FF00")
        else:
            st.info("Not enough history yet.")