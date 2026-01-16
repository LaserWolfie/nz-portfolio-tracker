from src.data.sheets import ensure_data_loaded
ensure_data_loaded()

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
from modules import utils

# --- CONFIGURATION ---
SHEET_NAME = "Share Portfolio" 
HISTORY_TAB_NAME = "History"
CHART_TAB_NAME = "chart_data"
BENCHMARK_TICKER = "^NZ50"
MACRO_SHEET_URL = "https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o"

# --- CUSTOM CSS ---
st.markdown("""
<style>
    .metric-card { background-color: #1E1E1E; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    .stMetric { background-color: transparent !important; }
</style>
""", unsafe_allow_html=True)

# --- HELPER FUNCTIONS ---
def clean_number(x):
    return utils.clean_number(x, nan_on_invalid=True)

def fix_ticker(t):
    """Fixes tickers."""
    t = str(t).strip().upper()
    if "PRIVATE" in t: return t
    if 'ASX:' in t: return t.replace('ASX:', '') + '.AX'
    if 'NZE:' in t: return t.replace('NZE:', '') + '.NZ'
    return t + '.NZ' if '.' not in t else t

def update_history_log(current_val):
    """Logs total value to History tab."""
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

# --- DATA LOADERS ---
@st.cache_data(ttl=3600)
def fetch_macro_history_benchmark():
    """Fetches Macro Data, History, and Benchmark in one go."""
    macro_data, hist_data = {}, []
    nzx_returns, nzx_1y = None, 0.0
    
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if "gcp_service_account" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        client = gspread.authorize(creds)
        
        # 1. Macro
        try:
            m_sheet = client.open_by_url(MACRO_SHEET_URL).worksheet("Dashboard")
            macro_data = {
                'regime_c2': m_sheet.acell('C2').value,
                'score': m_sheet.acell('C5').value,
                'sentiment': m_sheet.acell('C12').value,
                'signal': m_sheet.acell('C23').value,
                'eq': m_sheet.acell('C16').value,
                'bd': m_sheet.acell('C17').value,
                'al': m_sheet.acell('C18').value,
                'ca': m_sheet.acell('C19').value
            }
            # Policy Chart Data
            try:
                c_sheet = client.open_by_url(MACRO_SHEET_URL).worksheet(CHART_TAB_NAME)
                macro_data['chart'] = c_sheet.get_all_values()
            except: macro_data['chart'] = None
            macro_data['status'] = True
        except: macro_data['status'] = False

        # 2. History
        try:
            h_sheet = client.open(SHEET_NAME).worksheet(HISTORY_TAB_NAME)
            hist_data = h_sheet.get_all_values()
        except: hist_data = []
        
        # 3. Benchmark (NZX50)
        try:
            nzx = yf.download(BENCHMARK_TICKER, period="1y", progress=False)
            if isinstance(nzx.columns, pd.MultiIndex):
                nzx = nzx.xs('Close', axis=1, level=0) if 'Close' in nzx.columns.get_level_values(0) else nzx.iloc[:, 0]
            elif 'Close' in nzx.columns:
                nzx = nzx['Close']
            else:
                nzx = nzx.iloc[:, 0]
            if isinstance(nzx, pd.DataFrame): nzx = nzx.iloc[:, 0]
            
            nzx_returns = nzx.pct_change().dropna()
            nzx_1y = ((nzx.iloc[-1] - nzx.iloc[0]) / nzx.iloc[0]) * 100
        except: pass

        return macro_data, hist_data, nzx_returns, nzx_1y
        
    except Exception as e:
        return None, None, None, 0.0

# --- MAIN PAGE LOGIC ---
# 1. LOAD DATA
df_raw = st.session_state.stock_df.copy()
# 2. SIDEBAR
st.sidebar.header("🎛️ Strategy Engine")
strategy_mode = st.sidebar.radio(
    "Select Strategy:",
    ["Cycle Purist (Default)", "Momentum Chaser (Growth)", "Wealth Shield (Defensive)"]
)
refresh_requested = st.sidebar.button("🔄 Refresh Analysis")
if refresh_requested:
    st.session_state["refresh_stock_analysis"] = True
    st.rerun()

refresh_needed = st.session_state.pop("refresh_stock_analysis", False)

# 3. DATA CACHE
if not refresh_needed and "stock_macro_cache" in st.session_state:
    macro_data, hist_raw, nzx_returns, nzx_1y_val = st.session_state["stock_macro_cache"]
else:
    macro_data, hist_raw, nzx_returns, nzx_1y_val = fetch_macro_history_benchmark()
    st.session_state["stock_macro_cache"] = (macro_data, hist_raw, nzx_returns, nzx_1y_val)

st.title("🥝 NZ Portfolio Analyzer")

# 3. MACRO ENGINE
if macro_data and macro_data.get('status'):
    st.subheader(f"🧠 Active Strategy: {strategy_mode}")
    
    score = float(macro_data['score']) if macro_data['score'] else 0.0
    sentiment = macro_data.get('sentiment', 'Unknown')
    
    # Logic
    eq_tgt = clean_number(macro_data['eq']) / 100
    bd_tgt = clean_number(macro_data['bd']) / 100
    al_tgt = clean_number(macro_data['al']) / 100
    ca_tgt = clean_number(macro_data['ca']) / 100
    
    final_tgt = eq_tgt
    msg = "✅ Following Cycle Model."
    
    if strategy_mode == "Momentum Chaser (Growth)":
        if score > 0: final_tgt = 0.70; msg = "🚀 Economy Expanding. Ignoring warnings."
    elif strategy_mode == "Wealth Shield (Defensive)":
        if "Euphoric" in str(sentiment): final_tgt = 0.10; msg = "🛡️ Capping Equity at 10%."
        else: final_tgt = min(eq_tgt, 0.35); msg = "🛡️ Defensive Cap."

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Current Regime (C2)", macro_data.get('regime_c2', '-'), f"Signal: {macro_data.get('signal','-')}")
    m2.metric("Composite Score", f"{score}", "Range: -5 to +5")
    m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in str(sentiment) else "normal")
    m4.metric("Equity Target", f"{final_tgt*100:.0f}%", delta=f"Strategy: {strategy_mode.split(' ')[0]}")
    
    st.info(f"**Logic:** {msg}")
    
    # Altair Chart
    alloc_df = pd.DataFrame({
        "Asset": ["Equities", "Bonds", "Alternatives", "Cash"],
        "Allocation": [final_tgt, bd_tgt, al_tgt, ca_tgt]
    })
    base = alt.Chart(alloc_df).encode(x=alt.X('Allocation', axis=None), y=alt.Y('Asset', sort=None, title=None))
    bars = base.mark_bar().encode(color=alt.Color('Asset', legend=None), tooltip=['Asset', alt.Tooltip('Allocation', format='.1%')])
    text = base.mark_text(align='left', dx=5, color='white').encode(text=alt.Text('Allocation', format='.1%'))
    st.altair_chart((bars + text).properties(height=150), use_container_width=True)

st.markdown("---")

# 4. PORTFOLIO ENGINE
if not df_raw.empty:
    if not refresh_needed and "stock_portfolio_cache" in st.session_state:
        portfolio = st.session_state["stock_portfolio_cache"].copy()
        portfolio_is_fresh = False
    else:
        portfolio_is_fresh = True
        portfolio = df_raw[df_raw['Ticker'] != ''].copy()
        if 'Ticker' in portfolio.columns:
            ticker_series = portfolio['Ticker']
            ticker_clean = ticker_series.astype(str).str.strip()
            portfolio = portfolio[
                ticker_series.notna()
                & ticker_clean.ne('')
                & ~ticker_clean.str.contains(r"(?i)\btotal\b", regex=True, na=False)
            ].copy()
        portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    
        # Clean Numbers
        for c in ['Shares', 'Purchase Price', 'Value', 'Div Yield', 'P/E', 'Analyst Target']:
            if c in portfolio.columns: portfolio[c] = portfolio[c].apply(clean_number)
    
        portfolio = portfolio.dropna(subset=['Shares'])

        # Live Fetch
        public_tickers = [t for t in portfolio['Yahoo_Ticker'].tolist() if "PRIVATE" not in t]
    
        with st.spinner('📡 Connecting to Market Data...'):
            try:
                bulk = yf.download(public_tickers, period="1y", group_by='ticker', progress=False)
            except: bulk = pd.DataFrame()
            
        # Process
        res = {'curr':[], 'prev':[], 'p30':[], 'p1y':[], 'vol':[], 'pe':[], 'div':[], 'cap':[], 'upside':[], 'beta':[]}
    
        for idx, row in portfolio.iterrows():
            t = row['Yahoo_Ticker']
            curr = prev = p30 = p1y = 0.0
            vol_r = 0.0; beta_val = 1.0; pe = div = cap = upside = float('nan')
        
            # Private
            if "PRIVATE" in t:
                try: curr = row['Value'] / row['Shares']
                except: curr = row['Purchase Price']
                prev = p30 = p1y = curr
                div = row.get('Div Yield', 0)
        
            # Public
            elif not bulk.empty:
                try:
                    df_t = bulk[t] if len(public_tickers) > 1 else bulk
                    df_t = df_t.dropna(how='all')
                    if not df_t.empty and 'Close' in df_t.columns:
                        closes = df_t['Close']
                        curr = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2]) if len(closes)>1 else curr
                        p30 = float(closes.iloc[-22]) if len(closes)>22 else curr
                        p1y = float(closes.iloc[0])
                    
                        if 'Volume' in df_t.columns:
                            v_now = float(df_t['Volume'].iloc[-1])
                            v_avg = df_t['Volume'].iloc[-65:].mean()
                            vol_r = v_now/v_avg if v_avg>0 else 0
                        
                        # Beta
                        if nzx_returns is not None and len(closes) > 30:
                            s_ret = closes.pct_change().dropna()
                            aligned = pd.concat([s_ret, nzx_returns], axis=1, join='inner').dropna()
                            if len(aligned) > 20:
                                cov = aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
                                var = aligned.iloc[:, 1].var()
                                beta_val = cov/var if var!=0 else 1.0
                    
                        # Info
                        try:
                            info = yf.Ticker(t).info
                            pe = info.get('trailingPE', float('nan'))
                        
                            # DIVIDEND FIX: Handles decimal (0.05) vs percent (5.0) scaling
                            d_raw = info.get('dividendYield', 0) or 0
                            div = d_raw * 100
                        
                            cap = info.get('marketCap', 0)
                            tgt = info.get('targetMeanPrice', float('nan'))
                            if not pd.isna(tgt) and curr > 0: upside = ((tgt-curr)/curr)*100
                        except: pass
                except: pass
            
            if curr == 0: 
                try: curr = row['Value'] / row['Shares']
                except: curr = 0
            
            # Fallbacks to Sheet
            if pd.isna(pe) and 'P/E' in row: pe = row['P/E']
        
            # DIVIDEND FINAL CHECK
            if (pd.isna(div) or div == 0) and 'Div Yield' in row: 
                div = row['Div Yield']
        
            # SANITY CHECK: If > 30%, assume scaling error (e.g. 50 meaning 0.5 or 5.0)
            if not pd.isna(div) and div > 30: 
                div = div / 100
            
            res['curr'].append(curr); res['prev'].append(prev)
            res['p30'].append(p30); res['p1y'].append(p1y)
            res['vol'].append(vol_r); res['beta'].append(beta_val)
            res['pe'].append(pe); res['div'].append(div)
            res['cap'].append(cap); res['upside'].append(upside)

        # Assign
        portfolio['Current Price'] = res['curr']
        portfolio['Previous Price'] = res['prev']
        portfolio['Price 30d'] = res['p30']
        portfolio['Price 1y'] = res['p1y']
        portfolio['Vol Ratio'] = res['vol']
        portfolio['P/E Ratio'] = res['pe']
        portfolio['Div Yield %'] = res['div']
        portfolio['Market Cap'] = res['cap']
        portfolio['Analyst Upside'] = res['upside']
        portfolio['Beta'] = res['beta']

        # Calc
        portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
        portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
        portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
        portfolio['Day Change %'] = ((portfolio['Current Price'] - portfolio['Previous Price']) / portfolio['Previous Price']) * 100
        portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
        portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100
        portfolio['Est. Annual Income'] = portfolio['Market Value'] * (portfolio['Div Yield %'].fillna(0) / 100)

        st.session_state["stock_portfolio_cache"] = portfolio

    # --- DISPLAY METRICS ---
    total_val = portfolio['Market Value'].sum()
    st.subheader("📊 Portfolio Health")
    
    if portfolio_is_fresh and update_history_log(total_val): st.toast("✅ Wealth History Updated!")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Portfolio Value", f"${total_val:,.2f}")
    k2.metric("Total Profit", f"${(total_val - portfolio['Cost Basis'].sum()):,.2f}")
    k3.metric("Est. Dividends", f"${portfolio['Est. Annual Income'].sum():,.2f}")
    
    # Alpha Calc
    w_beta = (portfolio['Beta'] * (portfolio['Market Value']/total_val)).sum() if total_val > 0 else 1.0
    style = "Aggressive 🚀" if w_beta > 1.15 else ("Defensive 🛡️" if w_beta < 0.85 else "Balanced ⚖️")
    k4.metric("Risk Profile", f"{w_beta:.2f}", style)
    
    # Row 2 Metrics
    r1, r2, r3, r4 = st.columns(4)
    port_1y = (portfolio['1Y %'].fillna(0) * (portfolio['Market Value']/total_val)).sum() if total_val > 0 else 0
    alpha = port_1y - nzx_1y_val
    
    r1.metric("Portfolio 1Y Return", f"{port_1y:.1f}%")
    r2.metric("NZX50 1Y Return", f"{nzx_1y_val:.1f}%")
    r3.metric("vs NZX50 (Alpha)", f"{alpha:+.1f}%", delta_color="normal")
    r4.empty()

    # --- TABS ---
    tab1, tab2 = st.tabs(["🔎 Holdings Table", "📈 Wealth History"])
    
    with tab1:
        # HEATMAP
        disp_cols = ['Ticker', 'Vol Ratio', 'Market Cap', 'P/E Ratio', 'Analyst Upside', 'Current Price', 'Market Value', 'Day Change %', '30D %', '1Y %', 'Div Yield %', 'Total Gain %']
        final_cols = [c for c in disp_cols if c in portfolio.columns]
        
        st.dataframe(
            portfolio[final_cols].style.format({
                "Current Price": "${:.2f}", "Market Cap": "${:,.0f}", "Market Value": "${:,.0f}", 
                "Analyst Upside": "{:+.1f}%", "Day Change %": "{:+.1f}%", "30D %": "{:+.1f}%", 
                "1Y %": "{:+.1f}%", "Div Yield %": "{:.2f}%", "Total Gain %": "{:+.1f}%", 
                "P/E Ratio": "{:.1f}", "Vol Ratio": "{:.1f}x"
            })
            .background_gradient(subset=['Total Gain %', 'Analyst Upside'], cmap="RdYlGn", vmin=-20, vmax=20)
            .background_gradient(subset=['Day Change %', '30D %', '1Y %'], cmap="RdYlGn", vmin=-10, vmax=10)
            .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8)
            .background_gradient(subset=['Vol Ratio'], cmap="Reds", vmin=0.5, vmax=2.5)
            .background_gradient(subset=['P/E Ratio'], cmap="RdYlGn_r", vmin=5, vmax=40),
            use_container_width=True, height=500
        )
        
        # ALERTS & CONTEXT
        c_al, c_ch = st.columns(2)
        with c_al:
            st.markdown("##### 🚀 Analyst Opportunities")
            opps = portfolio[portfolio['Analyst Upside'] > 5].sort_values('Analyst Upside', ascending=False).head(3)
            for _, r in opps.iterrows(): st.success(f"**{r['Ticker']}**: {r['Analyst Upside']:.1f}% Upside")
        
        with c_ch:
            st.markdown("##### 📰 Market Context (Jan 2026)")
            st.info("""
            * **Infratil (IFT):** Rated BBB+ Investment Grade. Strong EBITDAF growth.
            * **EBOS Group (EBO):** Record earnings, driven by Healthcare segment.
            * **Skellerup (SKL):** FY26 Guidance upgraded.
            * **A2 Milk (ATM):** Upgraded Revenue Guidance.
            * **Macro:** Dairy prices recovering (+6.3%).
            """)
            
            st.markdown("##### 🔊 Volume Alerts")
            vol_alerts = portfolio[portfolio['Vol Ratio'] > 1.5]
            if not vol_alerts.empty:
                for _, r in vol_alerts.iterrows(): st.warning(f"**{r['Ticker']}**: High Volume ({r['Vol Ratio']:.1f}x)")
            else: st.success("✅ No Volume Spikes Today")

        # PIE CHARTS (Holdings & Sector)
        st.markdown("---")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.caption("Sector Allocation")
            if 'Sector' in portfolio.columns:
                s_counts = portfolio.groupby('Sector')['Market Value'].sum()
                fig, ax = plt.subplots(figsize=(5,5))
                fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
                ax.pie(s_counts, labels=s_counts.index, autopct='%1.0f%%', textprops={'color':'white'})
                st.pyplot(fig)
        with cc2:
            st.caption("Holdings Allocation")
            h_counts = portfolio.groupby('Ticker')['Market Value'].sum().sort_values(ascending=False).head(10)
            fig2, ax2 = plt.subplots(figsize=(5,5))
            fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
            ax2.pie(h_counts, labels=h_counts.index, autopct='%1.0f%%', textprops={'color':'white'})
            st.pyplot(fig2)

        # TOTAL RETURN BAR CHART
        st.markdown("---")
        st.subheader("Total Return by Stock")
        p_sort = portfolio.sort_values('Total Gain %', ascending=False)
        fig3, ax3 = plt.subplots(figsize=(10, 5))
        fig3.patch.set_facecolor('#0E1117'); ax3.set_facecolor('#0E1117')
        colors = ['#00FF00' if x >= 0 else '#FF0000' for x in p_sort['Total Gain %']]
        ax3.bar(p_sort['Ticker'], p_sort['Total Gain %'], color=colors)
        ax3.tick_params(colors='white', rotation=45)
        ax3.spines['bottom'].set_color('white'); ax3.spines['left'].set_color('white')
        ax3.set_ylabel("Return %", color='white')
        st.pyplot(fig3)

        # POLICY RATES CHART
        if macro_data.get('chart'):
            st.markdown("---")
            st.subheader("📉 Policy Rates (US vs NZ)")
            try:
                c_df = pd.DataFrame(macro_data['chart'][1:], columns=macro_data['chart'][0])
                c_df.set_index(c_df.columns[0], inplace=True)
                # Force numeric
                for c in c_df.columns: c_df[c] = pd.to_numeric(c_df[c], errors='coerce')
                st.line_chart(c_df)
            except: pass

    with tab2:
        st.subheader("📈 Wealth History")
        if hist_raw and len(hist_raw) > 1:
            try:
                h_df = pd.DataFrame(hist_raw[1:], columns=hist_raw[0])
                h_df['Date'] = pd.to_datetime(h_df['Date'])
                h_df['Value'] = pd.to_numeric(h_df['Value'])
                st.area_chart(h_df.set_index('Date')['Value'], color="#00FF00")
            except: st.warning("History format error.")
        else:
            st.info("No history data found.")
