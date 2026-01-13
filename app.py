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

# --- CACHED DATA FUNCTION (The Speed Fix) ---
@st.cache_data(ttl=3600) # Cache data for 1 hour or until manual refresh
def fetch_portfolio_data():
    """Fetches all data once so Strategy Toggles are instant."""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
            
        client = gspread.authorize(creds)
        
        # Open Sheets
        sheet = client.open("Share Portfolio").worksheet("Share Portfolio")
        
        # Try Macro Sheet
        macro_data = {}
        try:
            macro_sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o").worksheet("Dashboard")
            
            # Fetch specific Macro cells to dictionary
            macro_data['regime_c2'] = macro_sheet.acell('C2').value  # User requested C2
            macro_data['regime_c3'] = macro_sheet.acell('C3').value  # Fallback C3
            macro_data['score'] = macro_sheet.acell('C5').value
            macro_data['sentiment'] = macro_sheet.acell('C12').value # Corrected from C11
            macro_data['signal'] = macro_sheet.acell('C23').value
            
            # Asset Allocation Targets
            macro_data['alloc_equity'] = macro_sheet.acell('C16').value
            macro_data['alloc_bonds'] = macro_sheet.acell('C17').value
            macro_data['alloc_alts'] = macro_sheet.acell('C18').value
            macro_data['alloc_cash'] = macro_sheet.acell('C19').value
            
            # Policy Chart Data
            try:
                chart_sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1MRnuZCk9x317ApPxn_bMqI5q6FZAZO_qYJcDNkroq-o").worksheet("chart_data")
                macro_data['chart_values'] = chart_sheet.get_all_values()
            except: macro_data['chart_values'] = None
            
            macro_data['status'] = True
        except:
            macro_data['status'] = False

        # Portfolio Data
        data = sheet.get_all_values()
        df = pd.DataFrame(data[1:], columns=[str(h).strip() for h in data[0]])
        return df, macro_data, None
        
    except Exception as e:
        return None, None, str(e)

# --- HELPER FUNCTIONS ---
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

# --- SIDEBAR ---
st.sidebar.header("🎛️ Strategy Engine")
strategy_mode = st.sidebar.radio(
    "Select Strategy:",
    ["Cycle Purist (Default)", "Momentum Chaser (Growth)", "Wealth Shield (Defensive)"],
    help="Toggle strategies instantly without reloading data."
)

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear() # Clear cache to force fresh download
    st.rerun()

# --- MAIN LOGIC ---
df_raw, macro_data, error = fetch_portfolio_data()

if error:
    st.error(f"Connection Error: {error}")
    st.stop()

if df_raw is not None:
    # 1. PROCESS MACRO DATA (INSTANT TOGGLE)
    if macro_data and macro_data['status']:
        
        # REGIME FIX: Try C2 first, then C3
        regime_raw = macro_data.get('regime_c2')
        if not regime_raw or regime_raw in ['-', '']: 
            regime_raw = macro_data.get('regime_c3') # Fallback
        
        regime = regime_raw if regime_raw else "Loading..."
        signal = macro_data.get('signal', '-')
        score = float(macro_data['score']) if macro_data['score'] else 0.0
        sentiment = macro_data.get('sentiment', '-')
        
        # Targets
        eq_tgt = clean_number(macro_data['alloc_equity']) / 100
        bd_tgt = clean_number(macro_data['alloc_bonds']) / 100
        al_tgt = clean_number(macro_data['alloc_alts']) / 100
        ca_tgt = clean_number(macro_data['alloc_cash']) / 100
        
        # STRATEGY LOGIC
        final_tgt = eq_tgt
        logic_msg = "✅ Following Cycle Model exactly."
        
        if strategy_mode == "Momentum Chaser (Growth)":
            if score > 0: 
                final_tgt = 0.70
                logic_msg = "🚀 Economy is Expanding. Ignoring Sentiment warnings."
            else:
                logic_msg = "⚠️ Economy weak. Using system default."
        elif strategy_mode == "Wealth Shield (Defensive)":
            if "Euphoric" in str(sentiment): 
                final_tgt = 0.10
                logic_msg = "🛡️ Sentiment Euphoric. Capping Equity at 10%."
            else: 
                final_tgt = min(eq_tgt, 0.35)
                logic_msg = "🛡️ Defensive Cap active."

        # DISPLAY MACRO HEADER
        st.subheader(f"🧠 Active Strategy: {strategy_mode}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Regime Signal (C23)", signal, f"Macro: {regime}") # Uses C2/C3
        m2.metric("Composite Score", f"{score}", "Range: -5 (Restrictive) to +5 (Supportive)")
        m3.metric("Sentiment", sentiment, delta_color="inverse" if "Euphoric" in str(sentiment) else "normal")
        m4.metric("Equity Target", f"{final_tgt*100:.0f}%", delta=f"Strategy: {strategy_mode.split(' ')[0]}")
        st.info(f"**Strategy Logic:** {logic_msg}")
        
        # ASSET ALLOCATION CHART
        alloc_df = pd.DataFrame({
            "Asset": ["Equities", "Bonds", "Alternatives", "Cash"],
            "Allocation": [final_tgt, bd_tgt, al_tgt, ca_tgt] # Uses calculated target
        })
        
        base = alt.Chart(alloc_df).encode(y=alt.Y('Asset', sort=None, title=""), x=alt.X('Allocation', axis=None))
        bars = base.mark_bar().encode(color=alt.Color('Asset', legend=None), tooltip=['Asset', alt.Tooltip('Allocation', format='.1%')])
        text = base.mark_text(align='left', dx=5).encode(text=alt.Text('Allocation', format='.1%'))
        st.altair_chart((bars + text).properties(height=150), use_container_width=True)
        
        # POLICY RATE CHART
        if macro_data.get('chart_values'):
            try:
                raw = macro_data['chart_values']
                c_df = pd.DataFrame(raw[1:], columns=raw[0])
                for c in c_df.columns: 
                    if "Rate" in c or "OCR" in c: c_df[c] = pd.to_numeric(c_df[c], errors='coerce')
                
                with st.expander("📉 View Policy Rate Chart (US vs NZ)", expanded=False):
                    st.line_chart(c_df.set_index(c_df.columns[0]))
            except: pass

    st.markdown("---")

    # 2. PROCESS STOCK DATA (Only runs once due to cache, unless refreshed)
    portfolio = df_raw[df_raw['Ticker'] != ''].copy()
    portfolio['Yahoo_Ticker'] = portfolio['Ticker'].apply(fix_ticker)
    
    # Map columns
    col_map = {
        'Market Cap': next((c for c in portfolio.columns if 'Market' in c and 'Cap' in c), 'Market Cap'),
        'Analyst Target': next((c for c in portfolio.columns if 'Target' in c), 'Analyst Target'),
        'P/E': next((c for c in portfolio.columns if 'P/E' in c), 'P/E'),
        'Div Yield': next((c for c in portfolio.columns if 'Div' in c), 'Div Yield'),
        'Sector': next((c for c in portfolio.columns if 'Sector' in c), 'Sector')
    }
    
    # Process Manual Columns
    portfolio['Shares'] = portfolio['Shares'].apply(clean_number)
    portfolio['Purchase Price'] = portfolio['Purchase Price'].apply(clean_number)
    portfolio['Analyst Target'] = portfolio[col_map['Analyst Target']].apply(clean_number)
    portfolio = portfolio.dropna(subset=['Shares', 'Purchase Price'])

    # LIVE FETCH (Inside main run, but could be cached if needed. 
    # For now, we rely on the user not hitting refresh unless needed, 
    # but the Strategy Toggle above does NOT re-trigger this block if we isolate it properly.
    # Actually, Streamlit re-runs everything. To make Stock Fetching skip, we need to cache IT specifically.)
    
    @st.cache_data(ttl=900) # Cache Live Prices for 15 mins
    def get_live_prices(tickers):
        return yf.download(tickers, period="1y", group_by='ticker', progress=False)

    ticker_list = portfolio['Yahoo_Ticker'].tolist()
    if ticker_list:
        bulk_data = get_live_prices(ticker_list)
        
        # Build Data Lists
        curr_prices, p30_prices, p1y_prices, vol_ratios, daily_liquidities = [], [], [], [], []
        pe_ratios, div_yields, m_caps, upsides = [], [], [], []
        
        for idx, row in portfolio.iterrows():
            t = row['Yahoo_Ticker']
            # Price Data
            try:
                df_t = bulk_data[t] if len(ticker_list) > 1 else bulk_data
                curr = float(df_t['Close'].iloc[-1])
                p30 = float(df_t['Close'].iloc[-22])
                p1y = float(df_t['Close'].iloc[0])
                vol_now = float(df_t['Volume'].iloc[-1])
                vol_avg = df_t['Volume'].iloc[-65:].mean()
            except:
                curr = 0; p30 = 0; p1y = 0; vol_now = 0; vol_avg = 0
            
            curr_prices.append(curr); p30_prices.append(p30); p1y_prices.append(p1y)
            vol_ratios.append(vol_now / vol_avg if vol_avg > 0 else 0)
            daily_liquidities.append(vol_avg * curr)
            
            # Hybrid Data (Yahoo + Sheet)
            man_tgt = row['Analyst Target']
            try:
                info = yf.Ticker(t).info
                pe = info.get('trailingPE', float('nan'))
                div = (info.get('dividendYield', 0) or 0) * 100
                cap = info.get('marketCap', 0)
            except: pe = float('nan'); div = 0; cap = 0
            
            # Prefer Sheet Data if Yahoo fails
            sheet_pe = clean_number(row.get(col_map['P/E']))
            sheet_div = clean_number(row.get(col_map['Div Yield']))
            
            pe_ratios.append(pe if not pd.isna(pe) else sheet_pe)
            div_yields.append(div if div > 0 else sheet_div)
            m_caps.append(cap)
            
            # Upside
            if not pd.isna(man_tgt) and curr > 0: upsides.append(((man_tgt - curr)/curr)*100)
            else: upsides.append(float('nan'))

        # Assign back
        portfolio['Current Price'] = curr_prices
        portfolio['Price 30d'] = p30_prices
        portfolio['Price 1y'] = p1y_prices
        portfolio['Vol Ratio'] = vol_ratios
        portfolio['Daily Liquidity'] = daily_liquidities
        portfolio['P/E'] = pe_ratios
        portfolio['Div Yield %'] = div_yields
        portfolio['Market Cap'] = m_caps
        portfolio['Analyst Upside'] = upsides
        
        # Compute Gains
        portfolio['Market Value'] = portfolio['Shares'] * portfolio['Current Price']
        portfolio['Cost Basis'] = portfolio['Shares'] * portfolio['Purchase Price']
        portfolio['Total Gain %'] = ((portfolio['Market Value'] - portfolio['Cost Basis']) / portfolio['Cost Basis']) * 100
        
        # Heatmap Calcs
        portfolio['30D %'] = ((portfolio['Current Price'] - portfolio['Price 30d']) / portfolio['Price 30d']) * 100
        portfolio['1Y %'] = ((portfolio['Current Price'] - portfolio['Price 1y']) / portfolio['Price 1y']) * 100

        # --- VISUALS ---
        st.subheader("📊 Portfolio Health")
        total_val = portfolio['Market Value'].sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Portfolio Value", f"${total_val:,.2f}")
        c2.metric("Total Profit", f"${(total_val - portfolio['Cost Basis'].sum()):,.2f}")
        c3.metric("Top Mover (1Y)", f"{portfolio.sort_values('1Y %', ascending=False).iloc[0]['Ticker']}")

        st.dataframe(
            portfolio[['Ticker', 'Market Cap', 'Analyst Upside', 'Current Price', '30D %', '1Y %', 'Div Yield %', 'Total Gain %']].style.format({
                "Current Price": "${:.2f}", "Market Cap": "${:,.0f}", "Analyst Upside": "{:+.1f}%",
                "30D %": "{:+.1f}%", "1Y %": "{:+.1f}%", "Div Yield %": "{:.1f}%", "Total Gain %": "{:+.1f}%"
            })
            .background_gradient(subset=['Total Gain %', 'Analyst Upside', '30D %', '1Y %'], cmap="RdYlGn", vmin=-20, vmax=20),
            use_container_width=True, height=500
        )
        
        # Charts
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Sector Allocation")
            if 'Sector' in portfolio.columns:
                s_counts = portfolio.groupby('Sector')['Market Value'].sum()
                st.pyplot(plt.figure(figsize=(5,5)).gca().pie(s_counts, labels=s_counts.index, autopct='%1.0f%%')[0].figure)
        
        with c2:
            st.caption("Holdings Allocation")
            h_counts = portfolio.groupby('Ticker')['Market Value'].sum()
            st.pyplot(plt.figure(figsize=(5,5)).gca().pie(h_counts, labels=h_counts.index, autopct='%1.0f%%')[0].figure)