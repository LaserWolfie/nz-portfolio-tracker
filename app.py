import streamlit as st
import yfinance as yf
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
import time

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="NZ Portfolio Analyzer", page_icon="🥝", layout="wide")
st.title("🥝 NZ Portfolio Analyzer")

# --- DASHBOARD EXPLANATION ---
with st.expander("📘 Dashboard Guide"):
    st.markdown("""
    **1. Macro Strategy Engine:**
    * **Primary Signal:** "Regime Change" (Cell C23).
    * **Context:** "Current Regime" (Cell C3) & "Composite Score" (Cell C5).
    * **Allocation:** Pulls target weights for Equities, Bonds, Alts, and Cash (Section 4).
    
    **2. The "Hybrid" Data Engine:**
    * **Data:** Combines Yahoo Finance live data with your manual Sheet targets (Column AB).
    * **Safety:** Monitors Volume (1.5x spikes) and Liquidity (<$50k/day).
    """)

# --- CONFIGURATION ---
SHEET_NAME = "Share Portfolio" 
HISTORY_TAB_NAME = "History"
BENCHMARK_TICKER = "^NZ50"
MACRO_SHEET_URL = "https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o"

# --- SIDEBAR STRATEGY TOGGLE ---
st.sidebar.header("🎛️ Strategy Engine")
strategy_mode = st.sidebar.radio(
    "Select Strategy:",
    ["Cycle Purist (Default)", "Momentum Chaser (Growth)", "Wealth Shield (Defensive)"],
    help="Purist follows your sheet. Momentum ignores 'Euphoria' warnings. Shield caps risk."
)

# --- CONNECT TO GOOGLE SHEETS ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    
    # 1. Connect to Portfolio
    spreadsheet = client.open(SHEET_NAME)
    sheet = spreadsheet.worksheet("Share Portfolio")
    
    try:
        history_sheet = spreadsheet.worksheet(HISTORY_TAB_NAME)
    except:
        st.error(f"⚠️ Could not find a tab named '{HISTORY_TAB_NAME}'. Please create it.")
        st.stop()
        
    # 2. Connect to Macro Sheet
    try:
        macro_spreadsheet = client.open_by_url(MACRO_SHEET_URL)
        macro_sheet = macro_spreadsheet.worksheet("Dashboard")
        has_macro = True
    except Exception as e:
        st.sidebar.warning(f"Macro Sheet Link Failed: {e}")
        has_macro = False
    
    # --- DATA LOADING ---
    all_values = sheet.get_all_values()
    raw_headers = all_values[0]
    cleaned_headers = [str(h).strip() for h in raw_headers]
    df = pd.DataFrame(all_values[1:], columns=cleaned_headers)
    
    required_cols = ['Ticker', 'Shares', 'Purchase Price']
    for col in required_cols:
        if col not in df.columns:
            st.error(f"❌ Missing column: '{col}'. Check your sheet headers.")
            st.stop()
            
    # SMART MAPPING
    col_map = {
        'Market Cap': next((c for c in df.columns if 'Market' in c and 'Cap' in c), 'Market Cap'),
        'Analyst Target': next((c for c in df.columns if 'Target' in c), 'Analyst Target'),
        'P/E': next((c for c in df.columns if 'P/E' in c), 'P/E'),
        'Div Yield': next((c for c in df.columns if 'Div' in c and 'Yield' in c), 'Div Yield'),
        '52W High': next((c for c in df.columns if '52' in c and 'High' in c), '52W High'),
        '52W Low': next((c for c in df.columns if '52' in c and 'Low' in c), '52W Low'),
        'Sector': next((c for c in df.columns if 'Sector' in c), 'Sector')
    }

    portfolio = df.copy()
    portfolio = portfolio[portfolio['Ticker'] != '']

    # --- CLEANING FUNCTIONS ---
    def clean_number(x):
        """Robust cleaner"""
        if pd.isna(x) or x == '' or str(x).strip() in ['-', 'None', 'nan', 'N/A', '—']: return float('nan')
        if isinstance(x, (int, float)): return float(x)
        
        s = str(x).upper().replace(',', '').replace('$', '').replace(' ', '')
        multiplier = 1
        if 'M' in s: multiplier = 1_000_000; s = s.replace('M', '')
        elif 'B' in s: multiplier = 1_000_000_000; s = s.replace('B', '')
        
        s = s.replace('X', '').replace('%', '')
        try: return float(s) * multiplier
        except: return float('nan')

    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    
    if col_map['Analyst Target'] in portfolio.columns:
        portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    else:
        portfolio['Analyst Target'] = float('nan')

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
    
    try:
        hist_data = history_sheet.get_all_values()
        if len(hist_data) > 1:
            hist_df = pd.DataFrame(hist_data[1:], columns=hist_data[0])
            hist_df['Date'] = pd.to_datetime(hist_df['Date'])
            hist_df['Value'] = pd.to_numeric(hist_df['Value'])
            st.sidebar.subheader("📈 Wealth Trend")
            st.sidebar.line_chart(hist_df.set_index('Date')['Value'])
    except: pass 

except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# --- MAIN DASHBOARD ---
force_fresh = st.checkbox("Force Fresh Data (Ignore Sheet)", value=False)

if st.button("Run Full Analysis", type="primary"):
    
    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    
    # 1. FETCH PRICE & VOLUME
    with st.spinner('Fetching Market Data...'):
        try:
            bulk_data = yf.download(ticker_list, period="1y", group_by='ticker', progress=False)
            curr_prices, prev_prices, p30_prices, p1y_prices = [], [], [], []
            vol_ratios, daily_liquidities = [], []
            
            for t in ticker_list:
                try:
                    df_t = bulk_data[t] if len(ticker_list) > 1 else bulk_data
                    curr = float(df_t['Close'].iloc[-1])
                    prev = float(df_t['Close'].iloc[-2])
                    p30 = float(df_t['Close'].iloc[-22])
                    p1y = float(df_t['Close'].iloc[0])
                    
                    vol_now = float(df_t['Volume'].iloc[-1])
                    vol_avg = df_t['Volume'].iloc[-65:].mean()
                    
                    curr_prices.append(curr); prev_prices.append(prev)
                    p30_prices.append(p30); p1y_prices.append(p1y)
                    vol_ratios.append(vol_now / vol_avg if vol_avg > 0 else 0)
                    daily_liquidities.append(vol_avg * curr)
                except:
                    curr_prices.append(0); prev_prices.append(0); p30_prices.append(0); p1y_prices.append(0)
                    vol_ratios.append(0); daily_liquidities.append(0)

            portfolio['Current Price'] = curr_prices
            portfolio['Previous Price'] = prev_prices
            portfolio['Price 30d'] = p30_prices
            portfolio['Price 1y'] = p1y_prices
            portfolio['Vol Ratio'] = vol_ratios
            portfolio['Daily Liquidity'] = daily_liquidities
            
        except Exception as e:
            st.error(f"Data Error: {e}")

    # 2. HYBRID FETCH (DATA FILLING)
    progress = st.progress(0)
    final_pe, final_div, final_mcap, final_upside, final_targets = [], [], [], [], []
    final_52_lo, final_52_hi = [], []
    
    for i, row in portfolio.iterrows():
        progress.progress((i+1)/len(portfolio))
        t = row['Yahoo_Ticker']
        
        # Read Sheet Values
        curr_mcap = clean_number(row.get(col_map['Market Cap']))
        curr_target = clean_number(row.get(col_map['Analyst Target']))
        curr_pe = clean_number(row.get(col_map['P/E']))
        curr_div = clean_number(row.get(col_map['Div Yield']))
        curr_52h = clean_number(row.get(col_map['52W High']))
        curr_52l = clean_number(row.get(col_map['52W Low']))
        
        # Fetch if missing
        if pd.isna(curr_target) or pd.isna(curr_pe) or force_fresh:
            try:
                info = yf.Ticker(t).info
                if pd.isna(curr_target): curr_target = info.get('targetMeanPrice')
                if pd.isna(curr_pe): curr_pe = info.get('trailingPE')
                if pd.isna(curr_div): curr_div = (info.get('dividendYield', 0) or 0) * 100
                if pd.isna(curr_mcap): curr_mcap = info.get('marketCap')
                if pd.isna(curr_52h): curr_52h = info.get('fiftyTwoWeekHigh')
                if pd.isna(curr_52l): curr_52l = info.get('fiftyTwoWeekLow')
            except: pass

        # Store
        final_pe.append(curr_pe); final_div.append(curr_div); final_mcap.append(curr_mcap)
        final_52_hi.append(curr_52h); final_52_lo.append(curr_52l); final_targets.append(curr_target)
        
        price = portfolio.loc[i, 'Current Price']
        if not pd.isna(curr_target) and price > 0:
            final_upside.append(((curr_target - price) / price) * 100)
        else:
            final_upside.append(float('nan'))

    # ASSIGN COLUMNS (CRITICAL STEP)
    portfolio['P/E Ratio'] = final_pe
    portfolio['Div Yield %'] = final_div
    portfolio['Market Cap'] = final_mcap
    portfolio['Analyst Upside'] = final_upside
    portfolio['52W Low'] = final_52_lo
    portfolio['52W High'] = final_52_hi
    portfolio['Target Price'] = final_targets
    progress.empty()

    # 3. CALCULATIONS
    portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
    portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
    portfolio['Total Gain $'] = portfolio['Market Value'] - portfolio['Cost Basis']
    portfolio['Total Gain %'] = (portfolio['Total Gain $'] / portfolio['Cost Basis']) * 100
    portfolio['Day Change $'] = (portfolio['Current Price'] - portfolio['Previous Price']) * portfolio['Shares']
    
    # Heatmap Calcs
    portfolio['Day Change %'] = ((portfolio['Current Price'] - portfolio['Previous Price']) / portfolio['Previous Price']) * 100
    portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
    portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100
    portfolio['Est. Annual Income'] = portfolio['Market Value'] * (portfolio['Div Yield %'] / 100)

    total_val = portfolio['Market Value'].sum()
    total_profit = portfolio['Total Gain $'].sum()
    total_profit_pct = (total_profit / portfolio['Cost Basis'].sum()) * 100
    
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        hist = history_sheet.get_all_values()
        if len(hist) < 2 or hist[-1][0] != today:
            history_sheet.append_row([today, total_val])
    except: pass

    # --- MACRO STRATEGY ENGINE (COMPLETE) ---
    if has_macro:
        st.subheader(f"🧠 Active Strategy: {strategy_mode}")
        try:
            # SECTION 1 & 5: REGIME
            regime = macro_sheet.acell('C3').value 
            regime_change = macro_sheet.acell('C23').value 
            
            # SECTION 2: SCORE
            score_val = macro_sheet.acell('C5').value
            score = float(score_val) if score_val else 0.0
            sentiment = macro_sheet.acell('C11').value 
            
            # SECTION 4: TARGET ALLOCATION (FULL PULL)
            alloc_equity = clean_number(macro_sheet.acell('C16').value) / 100
            alloc_bonds = clean_number(macro_sheet.acell('C17').value) / 100
            alloc_alts = clean_number(macro_sheet.acell('C18').value) / 100
            alloc_cash = clean_number(macro_sheet.acell('C19').value) / 100

            if not regime or regime in ['-', '—']: regime = "Regime Loading..."
            
            # STRATEGY LOGIC
            target_pct = alloc_equity # Default
            if strategy_mode == "Momentum Chaser (Growth)":
                if score > 0: target_pct = 0.70; logic_msg = "🚀 Economy is Expanding. Ignoring Sentiment."
                else: logic_msg = "⚠️ Economy weak. Using system default."
            elif strategy_mode == "Wealth Shield (Defensive)":
                if "Euphoric" in str(sentiment): target_pct = 0.10; logic_msg = "🛡️ Sentiment Euphoric. Capping Equity at 10%."
                else: target_pct = min(alloc_equity, 0.35); logic_msg = "🛡️ Defensive Cap active."
            else:
                logic_msg = "✅ Following Cycle Model exactly."

            # DISPLAY METRICS
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Regime Change", regime_change, f"Current: {regime}")
            m2.metric("Composite Score", f"{score}", "Range: -5 to +5")
            m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in str(sentiment) else "normal")
            m4.metric("Equity Target", f"{target_pct*100:.0f}%", delta=f"Strategy: {strategy_mode.split(' ')[0]}")
            
            st.info(f"**Strategy Logic:** {logic_msg}")
            
            # ASSET ALLOCATION VISUAL (SECTION 4)
            st.caption("🎯 System Target Asset Allocation (Section 4)")
            alloc_data = pd.DataFrame({
                "Asset": ["Equities", "Bonds", "Alternatives", "Cash"],
                "Target": [alloc_equity, alloc_bonds, alloc_alts, alloc_cash]
            })
            st.bar_chart(alloc_data.set_index("Asset"), height=200)

        except Exception as e:
            st.warning(f"Macro Sync Issue: {e}")

    st.markdown("---")

    # --- INSIGHTS ---
    st.subheader("💡 Key Portfolio Insights")
    c_ins1, c_ins2 = st.columns(2)
    with c_ins1:
        st.markdown("##### 🚀 Analyst Opportunities")
        opps = portfolio[portfolio['Analyst Upside'] > 5].sort_values('Analyst Upside', ascending=False).head(3)
        for _, r in opps.iterrows(): st.success(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Upside (Target: ${r['Target Price']:.2f})")
        
        risks = portfolio[portfolio['Analyst Upside'] < -5].sort_values('Analyst Upside').head(3)
        for _, r in risks.iterrows(): st.error(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Downside (Target: ${r['Target Price']:.2f})")

    with c_ins2:
        st.markdown("##### 📰 Market Context (Jan 2026)")
        st.info("""
        * **Infratil (IFT):** Rated BBB+ Investment Grade. Strong EBITDAF growth.
        * **EBOS Group (EBO):** Record earnings, driven by Healthcare segment.
        * **Skellerup (SKL):** FY26 Guidance upgraded.
        * **A2 Milk (ATM):** Upgraded Revenue Guidance.
        * **Macro:** Dairy prices recovering (+6.3%).
        """)
        
        vol = portfolio[portfolio['Vol Ratio'] > 1.5]
        for _, r in vol.iterrows(): st.warning(f"**{r['Ticker']}**: High Volume ({r['Vol Ratio']:.1f}x average)")
        
        liq = portfolio[portfolio['Daily Liquidity'] < 50000]
        for _, r in liq.iterrows(): st.error(f"**{r['Ticker']}**: Low Liquidity (${r['Daily Liquidity']:,.0f}/day). Hard to sell.")

    # --- METRICS & TABLE ---
    st.markdown("---")
    st.subheader("📊 Portfolio Health")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Portfolio Value", f"${total_val:,.2f}")
    k2.metric("Total Profit", f"${total_profit:,.2f}", f"{total_profit_pct:.2f}%")
    k3.metric("Today's Gain", f"${portfolio['Day Change $'].sum():,.2f}")
    k4.metric("Est. Annual Income", f"${portfolio['Est. Annual Income'].sum():,.2f}")

    display_df = portfolio[['Ticker', 'Market Cap', 'Analyst Upside', 'Current Price', '52W Low', '52W High', 'Day Change %', '30D %', '1Y %', 'Vol Ratio', 'Daily Liquidity', 'Total Gain %', 'P/E Ratio', 'Div Yield %', 'Market Value']].copy()
    
    st.dataframe(
        display_df.style.format({
            "Current Price": "${:.2f}", "Market Value": "${:,.0f}", "Market Cap": "${:,.0f}",
            "Analyst Upside": "{:+.2f}%", "Total Gain %": "{:+.2f}%", "Day Change %": "{:+.2f}%",
            "30D %": "{:+.2f}%", "1Y %": "{:+.2f}%", "Div Yield %": "{:.2f}%",
            "Vol Ratio": "{:.1f}x", "Daily Liquidity": "${:,.0f}"
        }, na_rep="-")
        .background_gradient(subset=['Total Gain %'], cmap="RdYlGn", vmin=-50, vmax=50)
        .background_gradient(subset=['Analyst Upside'], cmap="RdYlGn", vmin=-10, vmax=30)
        .background_gradient(subset=['Day Change %'], cmap="RdYlGn", vmin=-5, vmax=5)
        .background_gradient(subset=['30D %'], cmap="RdYlGn", vmin=-10, vmax=10)
        .background_gradient(subset=['1Y %'], cmap="RdYlGn", vmin=-20, vmax=20)
        .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8)
        .background_gradient(subset=['Vol Ratio'], cmap="Reds", vmin=0.5, vmax=2.5),
        use_container_width=True, height=600
    )

    # CHARTS
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Composition by Sector")
        if 'Sector' in portfolio.columns:
            s_data = portfolio.groupby('Sector')['Market Value'].sum()
            fig, ax = plt.subplots(figsize=(5,5)); fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
            ax.pie(s_data, labels=s_data.index, autopct='%1.0f%%', textprops={'color':'white'})
            st.pyplot(fig)
            
    with c2:
        st.subheader("Composition by Stock")
        p_data = portfolio.groupby('Ticker')['Market Value'].sum().sort_values(ascending=False)
        fig2, ax2 = plt.subplots(figsize=(5,5)); fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
        ax2.pie(p_data, labels=p_data.index, autopct='%1.0f%%', textprops={'color':'white'})
        st.pyplot(fig2)

    st.markdown("---")
    st.subheader("Total Return")
    p_sort = portfolio.sort_values('Total Gain %', ascending=False)
    fig3, ax3 = plt.subplots(figsize=(10,5)); fig3.patch.set_facecolor('#0E1117'); ax3.set_facecolor('#0E1117')
    cols = ['#00FF00' if x >= 0 else '#FF0000' for x in p_sort['Total Gain %']]
    ax3.bar(p_sort['Ticker'], p_sort['Total Gain %'], color=cols)
    ax3.tick_params(axis='x', colors='white', rotation=45); ax3.tick_params(axis='y', colors='white')
    st.pyplot(fig3)