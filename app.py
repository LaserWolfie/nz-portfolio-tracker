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
    portfolio_spreadsheet = client.open(PORTFOLIO_SHEET_NAME)
    sheet = portfolio_spreadsheet.worksheet("Share Portfolio")
    
    try:
        macro_spreadsheet = client.open_by_url(MACRO_SHEET_URL)
        macro_sheet = macro_spreadsheet.worksheet("Dashboard")
        has_macro = True
    except:
        has_macro = False
    
    all_values = sheet.get_all_values()
    df = pd.DataFrame(all_values[1:], columns=[str(h).strip() for h in all_values[0]])
    
    col_map = {
        'Analyst Target': next((c for c in df.columns if 'Target' in c), 'Analyst Target'),
        'Sector': next((c for c in df.columns if 'Sector' in c), 'Sector'),
    }

    portfolio = df[df['Ticker'] != ''].copy()

    def clean_number(x):
        if pd.isna(x) or x == '' or str(x).strip() in ['-', 'None', 'nan']: return float('nan')
        s = str(x).upper().replace(',', '').replace('$', '').replace(' ', '').replace('%', '')
        try: return float(s)
        except: return float('nan')

    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price']) 

    def fix_ticker(t):
        t = str(t).strip().upper()
        if 'ASX:' in t: return t.replace('ASX:', '') + '.AX'
        if 'NZE:' in t: return t.replace('NZE:', '') + '.NZ'
        return t + '.NZ' if '.' not in t else t

    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    st.sidebar.success("✅ System Connected")

except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# --- MAIN ENGINE ---
if st.button("Run Strategy Analysis", type="primary"):
    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    
    with st.spinner('Fetching Real-Time Data...'):
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

    portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
    portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
    portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
    portfolio['Analyst Upside'] = ((portfolio['Analyst Target'] - portfolio['Current Price']) / portfolio['Current Price']) * 100
    total_equity_val = portfolio['Market Value'].sum()

    # --- MACRO STRATEGY ENGINE ---
    if has_macro:
        st.subheader(f"🧠 Active Strategy: {strategy_mode}")
        try:
            # Safer single-cell reads
            regime = macro_sheet.acell('C3').value 
            score = float(macro_sheet.acell('C5').value)
            sentiment = macro_sheet.acell('C11').value 
            sheet_target = clean_number(macro_sheet.acell('C16').value) / 100 
            
            # --- STRATEGY LOGIC ---
            if strategy_mode == "Momentum Chaser (Growth)":
                # If Economy is Positive (Score > 0), ignore Euphoria and go heavy
                if score > 0: 
                    target_pct = 0.70
                    logic_msg = "🚀 Economy is Expanding. Ignoring Sentiment warnings."
                else:
                    target_pct = sheet_target # Fallback to sheet if economy sucks
                    logic_msg = "⚠️ Economy is weak. Falling back to system defaults."
                    
            elif strategy_mode == "Wealth Shield (Defensive)":
                # Cap equity at 35% max, or 10% if Euphoric
                if "Euphoric" in sentiment:
                    target_pct = 0.10
                    logic_msg = "🛡️ Sentiment is Euphoric. Hard cap at 10% Equity."
                else:
                    target_pct = min(sheet_target, 0.35)
                    logic_msg = "🛡️ Capping Equity at 35% maximum."
                    
            else: # Cycle Purist
                target_pct = sheet_target
                logic_msg = "✅ Following Sheet Cycle Logic exactly."

            # Calculate Rebalancing
            target_val = (total_equity_val / target_pct) * target_pct # Simplified for equity-only view
            # More accurate gap calc assuming total_equity_val represents the equity portion of a larger portfolio
            # We estimate Total Portfolio Size = Current Equity / Current Allocation (estimated) 
            # But simpler is: Just tell user how much to change Equity to hit target relative to CASH they presumably have.
            
            # Better Logic: We treat 'total_equity_val' as the variable.
            # If target is 15%, and we have $100k equity, we need to know Total Cash to give perfect advice.
            # For now, we provide a "Directional" signal based on the Sheet's implied Total.
            
            # Let's assume the Sheet knows the Total Portfolio Value (Equity + Cash + Bonds)
            # Since we can't read your Cash balance from the app, we calculate the Gap based on *Equity Weighting*.
            
            gap_display = "Review Cash Reserves" # Placeholder if we can't calculate exact $ without Cash balance

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Macro Regime", regime)
            m2.metric("Macro Score", f"{score}")
            m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in sentiment else "normal")
            m4.metric("Target Equity %", f"{target_pct*100:.0f}%", delta=f"{target_pct - sheet_target:.2%}")
            
            st.info(f"**Strategy Logic:** {logic_msg}")
            
            # Visual Target Bar
            st.progress(target_pct, text=f"Target Allocation: {target_pct*100:.0f}%")
            
        except Exception as e:
            st.warning(f"Sync Error: Ensure Dashboard C3, C5, C11, C16 are populated. ({e})")

    st.markdown("---")
    
    # Holdings Table
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

    # Donut Charts
    c_pie1, c_pie2 = st.columns(2)
    with c_pie1:
        st.caption("By Sector")
        sector_data = portfolio.groupby(col_map['Sector'])['Market Value'].sum()
        fig1, ax1 = plt.subplots(figsize=(5,5)); fig1.patch.set_facecolor('#0E1117'); ax1.set_facecolor('#0E1117')
        ax1.pie(sector_data, labels=sector_data.index, autopct='%1.0f%%', textprops={'color':"white"}, pctdistance=0.8)
        fig1.gca().add_artist(plt.Circle((0,0),0.70,fc='#0E1117')); st.pyplot(fig1)

    with c_pie2:
        st.caption("By Stock")
        fig2, ax2 = plt.subplots(figsize=(5,5)); fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
        ax2.pie(portfolio['Market Value'], labels=portfolio['Ticker'], autopct='%1.0f%%', textprops={'color':"white"}, pctdistance=0.8)
        fig2.gca().add_artist(plt.Circle((0,0),0.70,fc='#0E1117')); st.pyplot(fig2)