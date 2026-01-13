import streamlit as st
import yfinance as yf
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
import altair as alt
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
    * **Regime Signal:** Combines "Regime Change" (C23) and "Current Regime" (C3).
    * **Strategy Toggles:**
        * *Cycle Purist:* Adheres strictly to the sheet's Equity Target.
        * *Momentum Chaser:* Overrides "Euphoria" if the Macro Score is positive.
        * *Wealth Shield:* Caps equity to protect capital during "Euphoria".
    
    **2. The "Hybrid" Data Engine:**
    * **Analyst Targets:** Prioritizes manual targets (Column AB) over Yahoo data.
    * **Liquidity:** Flags stocks trading <$50k/day.
    """)

# --- CONFIGURATION ---
SHEET_NAME = "Share Portfolio" 
HISTORY_TAB_NAME = "History"
CHART_TAB_NAME = "chart_data"
BENCHMARK_TICKER = "^NZ50"
MACRO_SHEET_URL = "https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o"

# --- SIDEBAR STRATEGY TOGGLE ---
st.sidebar.header("🎛️ Strategy Engine")
strategy_mode = st.sidebar.radio(
    "Select Strategy:",
    ["Cycle Purist (Default)", "Momentum Chaser (Growth)", "Wealth Shield (Defensive)"],
    help="Purist follows your sheet. Momentum ignores 'Euphoria' warnings. Shield caps risk."
)

# --- CACHED DATA FUNCTION ---
@st.cache_data(ttl=3600)
def fetch_data():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if "gcp_service_account" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            
        client = gspread.authorize(creds)
        
        # 1. Portfolio Data
        sheet = client.open(SHEET_NAME).worksheet("Share Portfolio")
        data = sheet.get_all_values()
        df = pd.DataFrame(data[1:], columns=[str(h).strip() for h in data[0]])
        
        # 2. Macro Data
        macro_data = {}
        try:
            m_sheet = client.open_by_url(MACRO_SHEET_URL).worksheet("Dashboard")
            # Pulling exact cells based on your screenshot
            macro_data['regime_c3'] = m_sheet.acell('C3').value  # "Expansion"
            macro_data['score'] = m_sheet.acell('C5').value      # "4"
            macro_data['sentiment'] = m_sheet.acell('C12').value # "Euphoric" (C12, not C11)
            macro_data['signal'] = m_sheet.acell('C23').value    # "Risk-On..."
            
            # Allocation
            macro_data['eq'] = m_sheet.acell('C16').value
            macro_data['bd'] = m_sheet.acell('C17').value
            macro_data['al'] = m_sheet.acell('C18').value
            macro_data['ca'] = m_sheet.acell('C19').value
            
            # Chart Data
            try:
                c_sheet = client.open_by_url(MACRO_SHEET_URL).worksheet(CHART_TAB_NAME)
                macro_data['chart'] = c_sheet.get_all_values()
            except: macro_data['chart'] = None
            
            macro_data['status'] = True
        except: macro_data['status'] = False
        
        return df, macro_data, None
    except Exception as e:
        return None, None, str(e)

# --- CLEANING HELPERS ---
def clean_number(x):
    if pd.isna(x) or str(x).strip() in ['', '-', 'None', 'nan', '—']: return float('nan')
    s = str(x).upper().replace(',', '').replace('$', '').replace(' ', '').replace('%', '')
    try: return float(s)
    except: return float('nan')

def fix_ticker(t):
    t = str(t).strip().upper()
    if 'ASX:' in t: return t.replace('ASX:', '') + '.AX'
    if 'NZE:' in t: return t.replace('NZE:', '') + '.NZ'
    return t + '.NZ' if '.' not in t else t

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# --- MAIN LOGIC ---
df_raw, macro_data, error = fetch_data()

if error:
    st.error(f"Connection Error: {error}")
    st.stop()

if df_raw is not None:
    # 1. MACRO SECTION
    if macro_data and macro_data['status']:
        st.subheader(f"🧠 Active Strategy: {strategy_mode}")
        
        # Extract & Clean
        regime = macro_data.get('regime_c3', '-')
        signal = macro_data.get('signal', '-')
        score = float(macro_data['score']) if macro_data['score'] else 0.0
        sentiment = macro_data.get('sentiment', 'Unknown')
        
        eq_tgt = clean_number(macro_data['eq']) / 100
        bd_tgt = clean_number(macro_data['bd']) / 100
        al_tgt = clean_number(macro_data['al']) / 100
        ca_tgt = clean_number(macro_data['ca']) / 100
        
        # Logic
        final_tgt = eq_tgt
        logic_msg = "✅ Following Cycle Model exactly."
        
        if strategy_mode == "Momentum Chaser (Growth)":
            if score > 0: 
                final_tgt = 0.70
                logic_msg = "🚀 Economy is Expanding. Ignoring Sentiment warnings."
            else: logic_msg = "⚠️ Economy weak. Using system default."
        elif strategy_mode == "Wealth Shield (Defensive)":
            if "Euphoric" in str(sentiment): 
                final_tgt = 0.10
                logic_msg = "🛡️ Sentiment Euphoric. Capping Equity at 10%."
            else: 
                final_tgt = min(eq_tgt, 0.35)
                logic_msg = "🛡️ Defensive Cap active."

        # Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Regime Signal (C23)", signal, f"Macro: {regime}")
        m2.metric("Composite Score", f"{score}", "Range: -5 (Restrictive) to +5 (Supportive)")
        m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in str(sentiment) else "normal")
        m4.metric("Equity Target", f"{final_tgt*100:.0f}%", delta=f"Strategy: {strategy_mode.split(' ')[0]}")
        st.info(f"**Strategy Logic:** {logic_msg}")
        
        # Horizontal Asset Allocation Chart
        st.caption("🎯 System Target Asset Allocation")
        alloc_df = pd.DataFrame({
            "Asset": ["Equities", "Bonds", "Alternatives", "Cash"],
            "Allocation": [final_tgt, bd_tgt, al_tgt, ca_tgt]
        })
        
        base = alt.Chart(alloc_df).encode(
            x=alt.X('Allocation', axis=None),
            y=alt.Y('Asset', sort=None, title=None)
        )
        bars = base.mark_bar().encode(
            color=alt.Color('Asset', legend=None),
            tooltip=['Asset', alt.Tooltip('Allocation', format='.1%')]
        )
        text = base.mark_text(align='left', dx=5, color='white').encode(
            text=alt.Text('Allocation', format='.1%')
        )
        st.altair_chart((bars + text).properties(height=200), use_container_width=True)
        
        # Policy Rate Chart
        if macro_data['chart']:
            try:
                c_df = pd.DataFrame(macro_data['chart'][1:], columns=macro_data['chart'][0])
                # Convert first column to date/index and others to numeric
                c_df.set_index(c_df.columns[0], inplace=True)
                for c in c_df.columns: c_df[c] = pd.to_numeric(c_df[c], errors='coerce')
                
                with st.expander("📉 View Policy Rate Chart (US vs NZ)", expanded=False):
                    st.line_chart(c_df)
            except: pass

    st.markdown("---")

    # 2. STOCK DATA PROCESSING
    portfolio = df_raw[df_raw['Ticker'] != ''].copy()
    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    
    # Map Columns
    col_map = {
        'Market Cap': next((c for c in portfolio.columns if 'Market' in c and 'Cap' in c), 'Market Cap'),
        'Analyst Target': next((c for c in portfolio.columns if 'Target' in c), 'Analyst Target'),
        'P/E': next((c for c in portfolio.columns if 'P/E' in c), 'P/E'),
        'Div Yield': next((c for c in portfolio.columns if 'Div' in c), 'Div Yield'),
        'Sector': next((c for c in portfolio.columns if 'Sector' in c), 'Sector')
    }
    
    # Clean Inputs
    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price'])

    # LIVE FETCH
    @st.cache_data(ttl=900)
    def get_live_prices(tickers):
        return yf.download(tickers, period="1y", group_by='ticker', progress=False)

    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    if ticker_list:
        bulk_data = get_live_prices(ticker_list)
        
        # Build Result Lists
        res = {'curr':[], 'p30':[], 'p1y':[], 'vol':[], 'liq':[], 'pe':[], 'div':[], 'cap':[], 'upside':[]}
        
        for idx, row in portfolio.iterrows():
            t = row['Yahoo_Ticker']
            # Prices
            try:
                df_t = bulk_data[t] if len(ticker_list) > 1 else bulk_data
                curr = float(df_t['Close'].iloc[-1])
                p30 = float(df_t['Close'].iloc[-22])
                p1y = float(df_t['Close'].iloc[0])
                v_now = float(df_t['Volume'].iloc[-1])
                v_avg = df_t['Volume'].iloc[-65:].mean()
            except: curr=0; p30=0; p1y=0; v_now=0; v_avg=0
            
            res['curr'].append(curr); res['p30'].append(p30); res['p1y'].append(p1y)
            res['vol'].append(v_now/v_avg if v_avg>0 else 0)
            res['liq'].append(v_avg * curr)
            
            # Fundamentals (Sheet priority for PE/Div)
            sheet_pe = clean_number(row.get(col_map['P/E']))
            sheet_div = clean_number(row.get(col_map['Div Yield']))
            
            # Fetch Yahoo if needed
            y_pe, y_div, y_cap = float('nan'), 0, 0
            if pd.isna(sheet_pe) or force_fresh:
                try: 
                    info = yf.Ticker(t).info
                    y_pe = info.get('trailingPE', float('nan'))
                    y_div = (info.get('dividendYield', 0) or 0) * 100
                    y_cap = info.get('marketCap', 0)
                except: pass
            
            res['pe'].append(y_pe if not pd.isna(y_pe) else sheet_pe)
            res['div'].append(y_div if y_div > 0 else sheet_div)
            res['cap'].append(y_cap)
            
            tgt = row['Analyst Target']
            if not pd.isna(tgt) and curr > 0: res['upside'].append(((tgt - curr)/curr)*100)
            else: res['upside'].append(float('nan'))

        # Assign Columns
        portfolio['Current Price'] = res['curr']
        portfolio['Price 30d'] = res['p30']
        portfolio['Price 1y'] = res['p1y']
        portfolio['Vol Ratio'] = res['vol']
        portfolio['Daily Liquidity'] = res['liq']
        portfolio['P/E Ratio'] = res['pe']
        portfolio['Div Yield %'] = res['div']
        portfolio['Market Cap'] = res['cap']
        portfolio['Analyst Upside'] = res['upside']
        
        # Final Calcs
        portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
        portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
        portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
        portfolio['Day Change %'] = ((portfolio['Current Price'] - portfolio.get('Previous Price', portfolio['Current Price'])) / portfolio['Current Price']) * 100 # Approx if no prev col
        portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
        portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100
        portfolio['Est. Annual Income'] = portfolio['Market Value'] * (portfolio['Div Yield %'] / 100)

        # --- DISPLAY ---
        st.subheader("📊 Portfolio Health")
        total_val = portfolio['Market Value'].sum()
        k1, k2, k3 = st.columns(3)
        k1.metric("Portfolio Value", f"${total_val:,.2f}")
        k2.metric("Total Profit", f"${(total_val - portfolio['Cost Basis'].sum()):,.2f}")
        k3.metric("Est. Dividends", f"${portfolio['Est. Annual Income'].sum():,.2f}")

        # Table
        st.dataframe(
            portfolio[['Ticker', 'Market Cap', 'Analyst Upside', 'Current Price', '30D %', '1Y %', 'Div Yield %', 'Vol Ratio', 'Total Gain %']].style.format({
                "Current Price": "${:.2f}", "Market Cap": "${:,.0f}", "Analyst Upside": "{:+.1f}%",
                "30D %": "{:+.1f}%", "1Y %": "{:+.1f}%", "Div Yield %": "{:.1f}%", "Total Gain %": "{:+.1f}%", "Vol Ratio": "{:.1f}x"
            })
            .background_gradient(subset=['Total Gain %', 'Analyst Upside'], cmap="RdYlGn", vmin=-20, vmax=20)
            .background_gradient(subset=['30D %', '1Y %'], cmap="RdYlGn", vmin=-10, vmax=10)
            .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8),
            use_container_width=True, height=500
        )

        # Charts (Restored and Fixed)
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Sector Allocation")
            if 'Sector' in portfolio.columns:
                s_counts = portfolio.groupby('Sector')['Market Value'].sum()
                fig, ax = plt.subplots(figsize=(5,5))
                fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
                ax.pie(s_counts, labels=s_counts.index, autopct='%1.0f%%', textprops={'color':'white'})
                st.pyplot(fig)
        
        with c2:
            st.caption("Holdings Allocation")
            h_counts = portfolio.groupby('Ticker')['Market Value'].sum().sort_values(ascending=False).head(10)
            fig2, ax2 = plt.subplots(figsize=(5,5))
            fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
            ax2.pie(h_counts, labels=h_counts.index, autopct='%1.0f%%', textprops={'color':'white'})
            st.pyplot(fig2)