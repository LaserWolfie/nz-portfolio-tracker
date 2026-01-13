import streamlit as st
import yfinance as yf
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime
import time

# --- CONFIGURATION ---
PORTFOLIO_SHEET_NAME = "Share Portfolio" 
MACRO_SHEET_URL = "https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o"

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="NZ Portfolio Manager", page_icon="🥝", layout="wide")
st.title("🥝 NZ Portfolio Manager")

# --- CONNECT TO GOOGLE SHEETS ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    
    # 1. Open Portfolio Sheet
    portfolio_spreadsheet = client.open(PORTFOLIO_SHEET_NAME)
    sheet = portfolio_spreadsheet.worksheet("Share Portfolio")
    history_sheet = portfolio_spreadsheet.worksheet("History")
    
    # 2. Open Macro Sheet
    try:
        macro_spreadsheet = client.open_by_url(MACRO_SHEET_URL)
        macro_sheet = macro_spreadsheet.worksheet("Dashboard")
        has_macro = True
    except Exception as e:
        st.sidebar.error(f"Macro Sheet Error: {e}")
        has_macro = False
    
    # --- DATA LOADING ---
    all_values = sheet.get_all_values()
    raw_headers = all_values[0]
    cleaned_headers = [str(h).strip() for h in raw_headers]
    df = pd.DataFrame(all_values[1:], columns=cleaned_headers)
    
    # Smart Mapping for columns (Maintaining AB column logic)
    col_map = {
        'Market Cap': next((c for c in df.columns if 'Market' in c and 'Cap' in c), 'Market Cap'),
        'Analyst Target': next((c for c in df.columns if 'Target' in c), 'Analyst Target'),
        'P/E': next((c for c in df.columns if 'P/E' in c), 'P/E'),
        'Div Yield': next((c for c in df.columns if 'Div' in c and 'Yield' in c), 'Div Yield'),
        'Sector': next((c for c in df.columns if 'Sector' in c), 'Sector'),
    }

    portfolio = df[df['Ticker'] != ''].copy()

    def clean_number(x):
        if pd.isna(x) or x == '' or str(x).strip() in ['-', 'None', 'nan']: return float('nan')
        s = str(x).upper().replace(',', '').replace('$', '').replace(' ', '').replace('%', '')
        multiplier = 1
        if 'M' in s: multiplier = 1_000_000; s = s.replace('M', '')
        elif 'B' in s: multiplier = 1_000_000_000; s = s.replace('B', '')
        try: return float(s) * multiplier
        except: return float('nan')

    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    # This reads the manual targets (ATM $9.55, EBO $37.25) from your sheet
    portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price']) 
    
    def fix_ticker(t):
        t = str(t).strip().upper()
        if 'ASX:' in t: return t.replace('ASX:', '') + '.AX'
        if 'NZE:' in t: return t.replace('NZE:', '') + '.NZ'
        if '.' not in t: return t + '.NZ'
        return t

    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    st.sidebar.success("✅ Dual-Sheet Sync Successful!")

except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# --- MAIN DASHBOARD ---
if st.button("Run Full Analysis", type="primary"):
    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    
    with st.spinner('Fetching live prices & volume...'):
        bulk_data = yf.download(ticker_list, period="1y", group_by='ticker', progress=False)
        curr_prices, vol_ratios, daily_liquidities = [], [], []
        
        for t in ticker_list:
            try:
                df_t = bulk_data[t] if len(ticker_list) > 1 else bulk_data
                curr = float(df_t['Close'].iloc[-1])
                vol_today = float(df_t['Volume'].iloc[-1])
                vol_avg = df_t['Volume'].iloc[-65:].mean()
                
                curr_prices.append(curr)
                vol_ratios.append(vol_today / vol_avg if vol_avg > 0 else 0)
                daily_liquidities.append(vol_avg * curr)
            except:
                curr_prices.append(0); vol_ratios.append(0); daily_liquidities.append(0)

        portfolio['Current Price'] = curr_prices
        portfolio['Vol Ratio'] = vol_ratios
        portfolio['Daily Liquidity'] = daily_liquidities

    # Portfolio Calculations
    portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
    portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
    portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
    # Manual Target vs Live Price
    portfolio['Analyst Upside'] = ((portfolio['Analyst Target'] - portfolio['Current Price']) / portfolio['Current Price']) * 100
    total_value = portfolio['Market Value'].sum()

    # --- MACRO OVERLAY (FIXED INDEXING) ---
    if has_macro:
        st.subheader("🧠 Macro Strategy Overlay")
        try:
            # Safer way to read specific cells to avoid IndexError
            regime = macro_sheet.acell('C3').value
            score = macro_sheet.acell('C5').value
            sentiment = macro_sheet.acell('C11').value
            target_raw = macro_sheet.acell('C16').value
            target_pct = clean_number(target_raw) / 100
            
            target_val = total_value * target_pct
            gap = total_value - target_val

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Regime", regime) # Expansion
            m2.metric("Macro Score", score) # 4
            m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in sentiment else "normal") # Euphoric
            
            action = "✅ On Target"
            if gap > 5000: action = f"⚠️ SELL ${gap:,.0f}"
            elif gap < -5000: action = f"🛒 BUY ${abs(gap):,.0f}"
            m4.metric("Action Signal", action, f"Target: {target_pct*100:.0f}%")
            st.progress(target_pct, text=f"System Target Allocation: {target_pct*100:.1f}%")
        except Exception as macro_err:
            st.warning(f"Could not parse Macro Dashboard cells: {macro_err}")

    # --- KEY INSIGHTS & ALERTS ---
    st.subheader("💡 Key Portfolio Insights")
    with st.expander("Opportunities & Alerts", expanded=True):
        c_ins1, c_ins2 = st.columns(2)
        with c_ins1:
            st.markdown("##### 🚀 Opportunities")
            opps = portfolio[portfolio['Analyst Upside'] > 5].sort_values('Analyst Upside', ascending=False).head(3)
            for _, r in opps.iterrows(): st.success(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Upside (Target: ${r['Analyst Target']:.2f})")
            
            st.markdown("##### ⚠️ Valuation Risks")
            risks = portfolio[portfolio['Analyst Upside'] < -5].sort_values('Analyst Upside').head(3)
            for _, r in risks.iterrows(): st.error(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Downside (Target: ${r['Analyst Target']:.2f})")
            
        with c_ins2:
            st.markdown("##### 🔊 Volume & Liquidity")
            # Vol Ratio Alert
            vol = portfolio[portfolio['Vol Ratio'] > 1.5]
            for _, r in vol.iterrows(): st.warning(f"**{r['Ticker']}**: High Volume ({r['Vol Ratio']:.1f}x average)")
            
            # Liquidity Alert
            liq = portfolio[portfolio['Daily Liquidity'] < 50000]
            for _, r in liq.iterrows(): st.error(f"**{r['Ticker']}**: Low Liquidity (${r['Daily Liquidity']:,.0f}/day)")

    st.markdown("---")
    
    # Holdings Table (Maintaining layout from image_203210.png)
    st.subheader("📊 Portfolio Performance")
    st.dataframe(
        portfolio[['Ticker', 'Analyst Upside', 'Current Price', 'Total Gain %', 'Vol Ratio', 'Daily Liquidity', 'Market Value']].style.format({
            "Current Price": "${:.2f}", "Market Value": "${:,.0f}", "Total Gain %": "{:+.2f}%", 
            "Analyst Upside": "{:+.2f}%", "Vol Ratio": "{:.1f}x", "Daily Liquidity": "${:,.0f}"
        }, na_rep="-")
        .background_gradient(subset=['Total Gain %'], cmap="RdYlGn", vmin=-50, vmax=50)
        .background_gradient(subset=['Analyst Upside'], cmap="RdYlGn", vmin=-10, vmax=30),
        use_container_width=True
    )

    # Donut Charts (Maintaining side-by-side from image_20324d.png)
    st.markdown("### 🥧 Composition")
    c_pie1, c_pie2 = st.columns(2)
    with c_pie1:
        st.caption("By Sector")
        if col_map['Sector'] in portfolio.columns:
            sector_data = portfolio.groupby(col_map['Sector'])['Market Value'].sum()
            fig1, ax1 = plt.subplots(figsize=(5,5)); fig1.patch.set_facecolor('#0E1117'); ax1.set_facecolor('#0E1117')
            # Use autopct for percentages shown in image_20324d.png
            ax1.pie(sector_data, labels=sector_data.index, autopct='%1.0f%%', textprops={'color':"white"}, pctdistance=0.85)
            # Create a donut hole
            centre_circle = plt.Circle((0,0),0.70,fc='#0E1117')
            fig1.gca().add_artist(centre_circle)
            st.pyplot(fig1)

    with c_pie2:
        st.caption("By Stock")
        fig2, ax2 = plt.subplots(figsize=(5,5)); fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
        ax2.pie(portfolio['Market Value'], labels=portfolio['Ticker'], autopct='%1.0f%%', textprops={'color':"white"}, pctdistance=0.85)
        # Create a donut hole
        centre_circle2 = plt.Circle((0,0),0.70,fc='#0E1117')
        fig2.gca().add_artist(centre_circle2)
        st.pyplot(fig2)