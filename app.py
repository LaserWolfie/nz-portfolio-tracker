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
    * **Regime Signal (C23):** Primary cycle indicator.
    * **Current Regime (C2):** The broader economic state (e.g., "Expansion").
    
    **2. The "Hybrid" Data Engine:**
    * **Public Stocks:** Live data from Yahoo Finance.
    * **Private Funds:** Tickers starting with 'PRIVATE' use the 'Current Price' from **Column D** of your sheet.
    * **Wealth Tracker:** Automatically logs daily total value to the 'History' tab.
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
        
        # 2. History Data
        try:
            hist_sheet = client.open(SHEET_NAME).worksheet(HISTORY_TAB_NAME)
            hist_data = hist_sheet.get_all_values()
        except: hist_data = []

        # 3. Macro Data
        macro_data = {}
        try:
            m_sheet = client.open_by_url(MACRO_SHEET_URL).worksheet("Dashboard")
            macro_data['regime_c2'] = m_sheet.acell('C2').value
            macro_data['score'] = m_sheet.acell('C5').value
            macro_data['sentiment'] = m_sheet.acell('C12').value
            macro_data['signal'] = m_sheet.acell('C23').value
            
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
        
        return df, macro_data, data, hist_data, None
    except Exception as e:
        return None, None, None, None, str(e)

# --- HISTORY UPDATER ---
def update_history_log(current_val):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if "gcp_service_account" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        client = gspread.authorize(creds)
        h_sheet = client.open(SHEET_NAME).worksheet(HISTORY_TAB_NAME)
        
        today = datetime.now().strftime("%Y-%m-%d")
        existing = h_sheet.get_all_values()
        
        if not existing or existing[-1][0] != today:
            h_sheet.append_row([today, current_val])
            return True
    except: return False
    return False

# --- CLEANING HELPERS ---
def clean_number(x):
    if pd.isna(x) or str(x).strip() in ['', '-', 'None', 'nan', '—']: return float('nan')
    s = str(x).upper().replace(',', '').replace('$', '').replace(' ', '').replace('%', '')
    try: return float(s)
    except: return float('nan')

def fix_ticker(t):
    t = str(t).strip().upper()
    if "PRIVATE" in t: return t
    if 'ASX:' in t: return t.replace('ASX:', '') + '.AX'
    if 'NZE:' in t: return t.replace('NZE:', '') + '.NZ'
    return t + '.NZ' if '.' not in t else t

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# --- MAIN LOGIC ---
df_raw, macro_data, raw_sheet_data, hist_raw, error = fetch_data()

if error:
    st.error(f"Connection Error: {error}")
    st.stop()

if df_raw is not None:
    # 1. MACRO SECTION
    if macro_data and macro_data['status']:
        st.subheader(f"🧠 Active Strategy: {strategy_mode}")
        
        regime = macro_data.get('regime_c2', '-')
        signal = macro_data.get('signal', '-')
        score = float(macro_data['score']) if macro_data['score'] else 0.0
        sentiment = macro_data.get('sentiment', 'Unknown')
        
        eq_tgt = clean_number(macro_data['eq']) / 100
        bd_tgt = clean_number(macro_data['bd']) / 100
        al_tgt = clean_number(macro_data['al']) / 100
        ca_tgt = clean_number(macro_data['ca']) / 100
        
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

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Current Regime (C2)", regime, f"Signal: {signal}")
        m2.metric("Composite Score", f"{score}", "Range: -5 (Restrictive) to +5 (Supportive)")
        m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in str(sentiment) else "normal")
        m4.metric("Equity Target", f"{final_tgt*100:.0f}%", delta=f"Strategy: {strategy_mode.split(' ')[0]}")
        st.info(f"**Strategy Logic:** {logic_msg}")
        
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

    st.markdown("---")

    # 2. STOCK DATA PROCESSING
    portfolio = df_raw[df_raw['Ticker'] != ''].copy()
    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    
    col_map = {
        'Analyst Target': next((c for c in portfolio.columns if 'Target' in c), 'Analyst Target'),
        'P/E': next((c for c in portfolio.columns if 'P/E' in c), 'P/E'),
        'Div Yield': next((c for c in portfolio.columns if 'Div' in c), 'Div Yield'),
        'Sector': next((c for c in portfolio.columns if 'Sector' in c), 'Sector')
    }
    
    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    if col_map['Analyst Target'] in portfolio.columns:
        portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price'])

    # LIVE FETCH
    public_tickers = [t for t in portfolio['Yahoo_Ticker'].tolist() if "PRIVATE" not in t]
    
    @st.cache_data(ttl=900)
    def get_live_prices(tickers):
        if not tickers: return None
        return yf.download(tickers, period="1y", group_by='ticker', progress=False)

    bulk_data = get_live_prices(public_tickers)
    
    # RESULT LISTS
    res = {'curr':[], 'prev':[], 'p30':[], 'p1y':[], 'vol':[], 'liq':[], 'pe':[], 'div':[], 'cap':[], 'upside':[]}
    
    for idx, row in portfolio.iterrows():
        t = row['Yahoo_Ticker']
        
        # --- CASE 1: PRIVATE INVESTMENT ---
        if "PRIVATE" in t:
            manual_price = 0
            try:
                # Force fetch from Column D (Index 3)
                raw_row = next(r for r in raw_sheet_data if str(r[0]).strip().upper() == str(row['Ticker']).strip().upper())
                if len(raw_row) > 3: manual_price = clean_number(raw_row[3])
            except: pass
            
            if manual_price == 0: manual_price = clean_number(row['Purchase Price'])

            res['curr'].append(manual_price); res['prev'].append(manual_price) 
            res['p30'].append(manual_price); res['p1y'].append(manual_price)
            res['vol'].append(0); res['liq'].append(0)
            res['pe'].append(0); res['div'].append(clean_number(row.get(col_map['Div Yield'])))
            res['cap'].append(0); res['upside'].append(0)
            
        # --- CASE 2: PUBLIC STOCK ---
        else:
            try:
                df_t = bulk_data[t] if len(public_tickers) > 1 else bulk_data
                closes = df_t['Close']
                curr = float(closes.iloc[-1]) if len(closes) > 0 else 0
                prev = float(closes.iloc[-2]) if len(closes) > 1 else curr
                p30 = float(closes.iloc[-22]) if len(closes) > 22 else curr
                p1y = float(closes.iloc[0]) if len(closes) > 0 else curr
                v_now = float(df_t['Volume'].iloc[-1])
                v_avg = df_t['Volume'].iloc[-65:].mean()
            except: curr=0; prev=0; p30=0; p1y=0; v_now=0; v_avg=0
            
            res['curr'].append(curr); res['prev'].append(prev); res['p30'].append(p30); res['p1y'].append(p1y)
            res['vol'].append(v_now/v_avg if v_avg>0 else 0)
            res['liq'].append(v_avg * curr)
            
            sheet_pe = clean_number(row.get(col_map['P/E']))
            sheet_div = clean_number(row.get(col_map['Div Yield']))
            
            y_pe, y_div, y_cap = float('nan'), 0, 0
            try: 
                info = yf.Ticker(t).info
                y_pe = info.get('trailingPE', float('nan'))
                y_div = (info.get('dividendYield', 0) or 0) * 100
                y_cap = info.get('marketCap', 0)
            except: pass
            
            final_div = y_div if y_div > 0 else sheet_div
            if not pd.isna(final_div) and final_div > 50: final_div = final_div / 100
            
            res['div'].append(final_div)
            res['pe'].append(y_pe if not pd.isna(y_pe) else sheet_pe)
            res['cap'].append(y_cap)
            
            tgt = row.get('Analyst Target', float('nan'))
            if not pd.isna(tgt) and curr > 0: res['upside'].append(((tgt - curr)/curr)*100)
            else: res['upside'].append(float('nan'))

    # Assign
    portfolio['Current Price'] = res['curr']
    portfolio['Previous Price'] = res['prev']
    portfolio['Price 30d'] = res['p30']
    portfolio['Price 1y'] = res['p1y']
    portfolio['Vol Ratio'] = res['vol']
    portfolio['Daily Liquidity'] = res['liq']
    portfolio['P/E Ratio'] = res['pe']
    portfolio['Div Yield %'] = res['div']
    portfolio['Market Cap'] = res['cap']
    portfolio['Analyst Upside'] = res['upside']
    
    # Calculations
    portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
    portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
    portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
    portfolio['Day Change %'] = ((portfolio['Current Price'] - portfolio['Previous Price']) / portfolio['Previous Price']) * 100
    portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
    portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100
    portfolio['Est. Annual Income'] = portfolio['Market Value'] * (portfolio['Div Yield %'].fillna(0) / 100)

    # --- DISPLAY ---
    st.subheader("📊 Portfolio Health")
    total_val = portfolio['Market Value'].sum()
    
    if update_history_log(total_val):
        st.toast("✅ Wealth History Updated!")

    k1, k2, k3 = st.columns(3)
    k1.metric("Portfolio Value", f"${total_val:,.2f}")
    k2.metric("Total Profit", f"${(total_val - portfolio['Cost Basis'].sum()):,.2f}")
    k3.metric("Est. Dividends", f"${portfolio['Est. Annual Income'].sum():,.2f}")

    tab1, tab2 = st.tabs(["🔎 Holdings Table", "📈 Wealth History"])
    
    with tab1:
        # TABLE (Added P/E HEATMAP)
        display_cols = ['Ticker', 'Vol Ratio', 'Market Cap', 'P/E Ratio', 'Analyst Upside', 'Current Price', 'Market Value', 'Day Change %', '30D %', '1Y %', 'Div Yield %', 'Total Gain %']
        
        st.dataframe(
            portfolio[display_cols].style.format({
                "Current Price": "${:.2f}", "Market Cap": "${:,.0f}", "Market Value": "${:,.0f}", 
                "Analyst Upside": "{:+.1f}%", "Day Change %": "{:+.1f}%", "30D %": "{:+.1f}%", 
                "1Y %": "{:+.1f}%", "Div Yield %": "{:.2f}%", "Total Gain %": "{:+.1f}%", 
                "P/E Ratio": "{:.1f}", "Vol Ratio": "{:.1f}x"
            })
            .background_gradient(subset=['Total Gain %', 'Analyst Upside'], cmap="RdYlGn", vmin=-20, vmax=20)
            .background_gradient(subset=['Day Change %', '30D %', '1Y %'], cmap="RdYlGn", vmin=-10, vmax=10)
            .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8)
            .background_gradient(subset=['Vol Ratio'], cmap="Reds", vmin=0.5, vmax=2.5)
            .background_gradient(subset=['P/E Ratio'], cmap="RdYlGn_r", vmin=5, vmax=40), # Low PE = Green
            use_container_width=True, height=500
        )

        # INSIGHTS
        st.subheader("💡 Alerts & Context")
        c_ins1, c_ins2 = st.columns(2)
        with c_ins1:
            st.markdown("##### 🚀 Analyst Opportunities")
            opps = portfolio[portfolio['Analyst Upside'] > 5].sort_values('Analyst Upside', ascending=False).head(3)
            for _, r in opps.iterrows(): st.success(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Upside")
            
        with c_ins2:
            st.markdown("##### 📰 Market Context (Jan 2026)")
            st.info("""
            * **Infratil (IFT):** Rated BBB+ Investment Grade. Strong EBITDAF growth.
            * **EBOS Group (EBO):** Record earnings, driven by Healthcare segment.
            * **Skellerup (SKL):** FY26 Guidance upgraded.
            * **A2 Milk (ATM):** Upgraded Revenue Guidance.
            * **Macro:** Dairy prices recovering (+6.3%).
            """)
            vol_alerts = portfolio[portfolio['Vol Ratio'] > 1.5]
            if not vol_alerts.empty:
                for _, r in vol_alerts.iterrows(): st.warning(f"**{r['Ticker']}**: High Volume ({r['Vol Ratio']:.1f}x)")
            else: st.success("✅ No Volume Spikes Today")

        # CHARTS
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

        # Returns
        st.markdown("---")
        st.subheader("Total Return by Stock")
        p_sort = portfolio.sort_values('Total Gain %', ascending=False)
        fig3, ax3 = plt.subplots(figsize=(10, 5))
        fig3.patch.set_facecolor('#0E1117'); ax3.set_facecolor('#0E1117')
        colors = ['#00FF00' if x >= 0 else '#FF0000' for x in p_sort['Total Gain %']]
        ax3.bar(p_sort['Ticker'], p_sort['Total Gain %'], color=colors)
        ax3.set_ylabel("Total Gain %", color="white")
        ax3.tick_params(axis='x', colors='white', rotation=45)
        ax3.tick_params(axis='y', colors='white')
        ax3.spines['bottom'].set_color('white'); ax3.spines['left'].set_color('white')
        ax3.spines['top'].set_visible(False); ax3.spines['right'].set_visible(False)
        st.pyplot(fig3)

        # Policy Chart
        if macro_data.get('chart'):
            st.markdown("---")
            st.subheader("📉 Policy Rates (US vs NZ)")
            try:
                c_df = pd.DataFrame(macro_data['chart'][1:], columns=macro_data['chart'][0])
                c_df.set_index(c_df.columns[0], inplace=True)
                for c in c_df.columns: c_df[c] = pd.to_numeric(c_df[c], errors='coerce')
                st.line_chart(c_df)
            except: pass

    # --- WEALTH HISTORY TAB ---
    with tab2:
        st.subheader("📈 Wealth History")
        if hist_raw and len(hist_raw) > 1:
            try:
                h_df = pd.DataFrame(hist_raw[1:], columns=hist_raw[0])
                h_df['Date'] = pd.to_datetime(h_df['Date'])
                h_df['Value'] = pd.to_numeric(h_df['Value'])
                st.area_chart(h_df.set_index('Date')['Value'], color="#00FF00")
            except: st.warning("Could not load history.")
        else:
            st.info("No history data found. Today's value has been logged.")