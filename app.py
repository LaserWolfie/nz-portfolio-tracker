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

# --- CONFIGURATION ---
SHEET_NAME = "Share Portfolio" 
HISTORY_TAB_NAME = "History"
BENCHMARK_TICKER = "^NZ50"

# --- CONNECT TO GOOGLE SHEETS ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    spreadsheet = client.open(SHEET_NAME)
    sheet = spreadsheet.worksheet("Share Portfolio")
    
    try:
        history_sheet = spreadsheet.worksheet(HISTORY_TAB_NAME)
    except:
        st.error(f"⚠️ Could not find a tab named '{HISTORY_TAB_NAME}'. Please create it.")
        st.stop()
    
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
        'Insider': next((c for c in df.columns if 'Insider' in c), 'Insider Activity'),
        'Sector': 'Sector'
    }

    portfolio = df.copy()
    portfolio = portfolio[portfolio['Ticker'] != '']

    # --- CLEANING FUNCTIONS ---
    def clean_number(x):
        """Robust cleaner"""
        if pd.isna(x) or x == '' or str(x).strip() in ['-', 'None', 'nan', 'N/A']: return float('nan')
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
    
    # --- STEP 1: BENCHMARK ---
    market_return_pct = 0.0
    market_hist_data = None
    with st.spinner('Fetching Benchmark...'):
        try:
            market_data = yf.download(BENCHMARK_TICKER, period="1y", progress=False)
            if 'Close' in market_data.columns: market_hist_data = market_data['Close']
            else: market_hist_data = market_data
            if isinstance(market_hist_data, pd.DataFrame): market_hist_data = market_hist_data.iloc[:, 0]
            if len(market_hist_data) >= 2:
                market_now = float(market_hist_data.iloc[-1])
                market_prev = float(market_hist_data.iloc[-2])
                market_return_pct = ((market_now - market_prev) / market_prev) * 100
        except: pass

    # --- STEP 2: BULK PRICE & VOLUME HISTORY ---
    with st.spinner('Fetching prices & volume...'):
        try:
            bulk_data = yf.download(ticker_list, period="1y", group_by='ticker', progress=False)
            curr_prices, prev_prices, p30_prices, p1y_prices = [], [], [], []
            vol_ratios, daily_liquidities = [], []
            betas = []
            
            if market_hist_data is not None: market_returns = market_hist_data.pct_change().dropna()
            else: market_returns = pd.Series([])

            for t in ticker_list:
                try:
                    if len(ticker_list) == 1: df_t = bulk_data
                    else: df_t = bulk_data[t]
                    df_t = df_t.dropna(how='all')
                    
                    if not df_t.empty and 'Close' in df_t.columns:
                        closes = df_t['Close']
                        volumes = df_t['Volume'] if 'Volume' in df_t.columns else pd.Series([0]*len(closes))
                        
                        curr = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2]) if len(closes) >= 2 else curr
                        p30 = float(closes.iloc[-22]) if len(closes) >= 22 else (float(closes.iloc[0]) if len(closes) > 0 else curr)
                        p1y = float(closes.iloc[0]) if len(closes) > 0 else curr
                        
                        # VOLUME CALCS
                        vol_today = float(volumes.iloc[-1])
                        vol_avg = volumes.iloc[-65:].mean() if len(volumes) > 0 else 0
                        
                        if vol_avg > 0: v_ratio = vol_today / vol_avg
                        else: v_ratio = 0.0
                        
                        liquidity = vol_avg * curr

                        # BETA CALC
                        beta_val = 1.0
                        if len(closes) > 30 and len(market_returns) > 30:
                            stock_ret = closes.pct_change().dropna()
                            aligned = pd.concat([stock_ret, market_returns], axis=1).dropna()
                            if len(aligned) > 10:
                                cov = aligned.cov().iloc[0, 1]
                                var = aligned.iloc[:, 1].var()
                                if var != 0: beta_val = cov / var

                        curr_prices.append(curr); prev_prices.append(prev)
                        p30_prices.append(p30); p1y_prices.append(p1y)
                        betas.append(beta_val)
                        vol_ratios.append(v_ratio)
                        daily_liquidities.append(liquidity)
                    else: raise ValueError("No data")
                except:
                    curr_prices.append(0.0); prev_prices.append(0.0)
                    p30_prices.append(0.0); p1y_prices.append(0.0); betas.append(1.0)
                    vol_ratios.append(0.0); daily_liquidities.append(0.0)

            portfolio['Current Price'] = curr_prices
            portfolio['Previous Price'] = prev_prices
            portfolio['Price 30d'] = p30_prices
            portfolio['Price 1y'] = p1y_prices
            portfolio['Beta'] = betas
            portfolio['Vol Ratio'] = vol_ratios
            portfolio['Daily Liquidity'] = daily_liquidities
            
        except Exception as e:
            st.error(f"Data Error: {e}"); st.stop()

    # --- STEP 3: HYBRID ANALYST FETCH ---
    progress = st.progress(0); status = st.empty()
    
    final_pe, final_div, final_mcap, final_upside, final_targets = [], [], [], [], []
    final_52_lo, final_52_hi = [], []
    
    pending_updates = []

    for i, row in portfolio.iterrows():
        t = row['Yahoo_Ticker']
        status.text(f"Analysing {t}...")
        progress.progress((i+1)/len(portfolio))
        
        # 1. READ SHEET
        if force_fresh:
            curr_mcap = curr_target = curr_pe = curr_div_pct = curr_52h = curr_52l = float('nan')
        else:
            curr_mcap = clean_number(row.get(col_map['Market Cap']))
            curr_target = clean_number(row.get(col_map['Analyst Target']))
            curr_pe = clean_number(row.get(col_map['P/E']))
            curr_div_pct = clean_number(row.get(col_map['Div Yield']))
            curr_52h = clean_number(row.get(col_map['52W High']))
            curr_52l = clean_number(row.get(col_map['52W Low']))
        
        # DIVIDEND FIX
        if not pd.isna(curr_div_pct) and curr_div_pct > 30: 
            curr_div_pct = curr_div_pct / 100

        # NO TARGET SANITY CHECK (Trust User Data)
        price_now = portfolio.loc[i, 'Current Price']

        # 2. FETCH MISSING
        fetch_needed = False
        if pd.isna(curr_target) or pd.isna(curr_pe) or pd.isna(curr_div_pct):
            fetch_needed = True

        if fetch_needed:
            try:
                stock = yf.Ticker(t)
                try:
                    if hasattr(stock, 'fast_info'):
                        if pd.isna(curr_mcap) and stock.fast_info.market_cap:
                            curr_mcap = stock.fast_info.market_cap
                            if col_map['Market Cap'] in df.columns:
                                pending_updates.append((i+2, df.columns.get_loc(col_map['Market Cap'])+1, curr_mcap))
                        if pd.isna(curr_52h) and stock.fast_info.year_high:
                            curr_52h = stock.fast_info.year_high
                            if col_map['52W High'] in df.columns:
                                pending_updates.append((i+2, df.columns.get_loc(col_map['52W High'])+1, curr_52h))
                        if pd.isna(curr_52l) and stock.fast_info.year_low:
                            curr_52l = stock.fast_info.year_low
                            if col_map['52W Low'] in df.columns:
                                pending_updates.append((i+2, df.columns.get_loc(col_map['52W Low'])+1, curr_52l))
                except: pass

                try:
                    info = stock.info
                    tgt = info.get('targetMeanPrice') or info.get('targetMedianPrice')
                    if tgt and pd.isna(curr_target):
                        curr_target = tgt
                        if col_map['Analyst Target'] in df.columns:
                            pending_updates.append((i+2, df.columns.get_loc(col_map['Analyst Target'])+1, curr_target))

                    pe = info.get('trailingPE')
                    if pe and pd.isna(curr_pe):
                        curr_pe = pe
                        if col_map['P/E'] in df.columns:
                            pending_updates.append((i+2, df.columns.get_loc(col_map['P/E'])+1, curr_pe))

                    div = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
                    if div and pd.isna(curr_div_pct):
                        curr_div_pct = div * 100
                        if col_map['Div Yield'] in df.columns:
                             pending_updates.append((i+2, df.columns.get_loc(col_map['Div Yield'])+1, curr_div_pct))
                    time.sleep(0.3)
                except: pass
            except: pass

        # 3. Store
        final_pe.append(curr_pe)
        final_div.append(curr_div_pct)
        final_mcap.append(curr_mcap)
        final_52_hi.append(curr_52h)
        final_52_lo.append(curr_52l)
        final_targets.append(curr_target)
        
        if not pd.isna(curr_target) and price_now > 0:
            upside_val = ((curr_target - price_now) / price_now) * 100
        else:
            upside_val = float('nan')
        final_upside.append(upside_val)

    # --- BATCH SAVE ---
    if pending_updates and not force_fresh:
        status.text(f"Saving {len(pending_updates)} new data points...")
        try:
            for row_idx, col_idx, val in pending_updates:
                try:
                    sheet.update_cell(row_idx, col_idx, val)
                    time.sleep(0.2)
                except: pass
        except: pass

    status.empty(); progress.empty()
    
    portfolio['P/E Ratio'] = final_pe
    portfolio['Div Yield %'] = final_div
    portfolio['Market Cap'] = final_mcap
    portfolio['Analyst Upside'] = final_upside
    portfolio['52W Low'] = final_52_lo
    portfolio['52W High'] = final_52_hi
    portfolio['Target Price'] = final_targets

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

    total_value = portfolio['Market Value'].sum()
    total_cost = portfolio['Cost Basis'].sum()
    total_profit_val = total_value - total_cost
    total_profit_pct = (total_profit_val / total_cost) * 100 if total_cost > 0 else 0
    day_gain_val = portfolio['Day Change $'].sum()
    est_income = portfolio['Est. Annual Income'].sum()
    yield_on_market = (est_income / total_value) * 100 if total_value > 0 else 0
    
    portfolio['Weight'] = portfolio['Market Value'] / total_value
    portfolio_beta = (portfolio['Weight'] * portfolio['Beta']).sum() if total_value > 0 else 1.0

    if portfolio_beta > 1.15: risk_label = "Aggressive 🚀"; risk_msg = "Growth focus."
    elif portfolio_beta < 0.85: risk_label = "Defensive 🛡️"; risk_msg = "Preservation focus."
    else: risk_label = "Balanced ⚖️"; risk_msg = "Market tracking."

    today_str = datetime.now().strftime("%Y-%m-%d")
    existing_history = history_sheet.get_all_values()
    if len(existing_history) < 2 or existing_history[-1][0] != today_str:
        history_sheet.append_row([today_str, total_value])

    # --- INSIGHTS & ALERTS ---
    st.subheader("💡 Key Portfolio Insights")
    with st.expander("View Opportunities, Market Context & Alerts", expanded=True):
        col_insight_1, col_insight_2 = st.columns(2)
        
        with col_insight_1:
            st.markdown("##### 🚀 Analyst Opportunities")
            opps = portfolio[portfolio['Analyst Upside'] > 5].sort_values(by='Analyst Upside', ascending=False).head(3)
            if not opps.empty:
                for _, row in opps.iterrows():
                    st.success(f"**{row['Ticker']}**: {row['Analyst Upside']:.1f}% Upside (Target: ${row['Target Price']:.2f})")
            else: st.info("No major upside opportunities detected.")

            st.markdown("##### ⚠️ Valuation Risks")
            risks = portfolio[portfolio['Analyst Upside'] < -5].sort_values(by='Analyst Upside').head(3)
            if not risks.empty:
                for _, row in risks.iterrows():
                    st.error(f"**{row['Ticker']}**: {row['Analyst Upside']:.1f}% Downside (Target: ${row['Target Price']:.2f})")
            else: st.success("No major valuation risks detected.")

        with col_insight_2:
            st.markdown("##### 📰 Market Context (Jan 2026)")
            st.info("""
            * **Infratil (IFT):** Rated BBB+ Investment Grade. Strong EBITDAF growth.
            * **EBOS Group (EBO):** Record earnings, driven by Healthcare segment.
            * **Skellerup (SKL):** FY26 Guidance upgraded.
            * **A2 Milk (ATM):** Upgraded Revenue Guidance.
            * **Macro:** Dairy prices recovering (+6.3%).
            """)
            
            st.markdown("##### 🔊 Volume & Liquidity Alerts")
            # 1. Volume Spikes
            vol_spikes = portfolio[portfolio['Vol Ratio'] > 1.5].sort_values(by='Vol Ratio', ascending=False)
            if not vol_spikes.empty:
                for _, r in vol_spikes.iterrows():
                    st.warning(f"**{r['Ticker']}**: High Volume ({r['Vol Ratio']:.1f}x average)")
            
            # 2. Low Liquidity Warning
            low_liq = portfolio[portfolio['Daily Liquidity'] < 50000].sort_values(by='Daily Liquidity')
            if not low_liq.empty:
                for _, r in low_liq.iterrows():
                    st.error(f"**{r['Ticker']}**: Low Liquidity (${r['Daily Liquidity']:,.0f}/day). Hard to sell.")
            
            if vol_spikes.empty and low_liq.empty:
                st.caption("✅ No volume or liquidity risks today.")

    st.markdown("---")

    # --- METRICS UI ---
    st.subheader("📊 Portfolio Health")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Value", f"${total_value:,.2f}")
    c2.metric("Total Profit", f"${total_profit_val:,.2f}", f"{total_profit_pct:.2f}%")
    c3.metric("Today's Gain", f"${day_gain_val:,.2f}")
    c4.metric("Est. Dividends/Yr", f"${est_income:,.2f}", f"{yield_on_market:.2f}% Yield")

    st.markdown("---")
    st.subheader("🧠 Risk & Benchmark")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("NZX 50 Today", f"{market_return_pct:+.2f}%")
    b2.metric("Alpha", f"{(day_gain_val/(total_value-day_gain_val)*100 - market_return_pct):+.2f}%")
    b3.metric("Beta", f"{portfolio_beta:.2f}")
    b4.metric("Strategy", risk_label)

    st.markdown("---")
    tab1, tab2 = st.tabs(["🔎 Holdings Table", "📈 Wealth History"])
    
    with tab1:
        # TABLE PREP
        display_df = portfolio[['Ticker', 'Market Cap', 'Analyst Upside', 'Current Price', '52W Low', '52W High', 'Day Change %', '30D %', '1Y %', 'Vol Ratio', 'Daily Liquidity', 'Total Gain %', 'P/E Ratio', 'Div Yield %', 'Market Value']].copy()
        
        # CLICKABLE URLS
        display_df['URL'] = "https://finance.yahoo.com/quote/" + portfolio['Yahoo_Ticker']
        display_df['Ticker'] = display_df['URL']
        
        # HEATMAP FIX
        for c in ['Day Change %', '30D %', '1Y %', 'Total Gain %', 'Analyst Upside', 'Div Yield %', 'Vol Ratio']:
            display_df[c] = display_df[c].astype(str).str.replace('%', '', regex=False).str.replace(',', '', regex=False)
            display_df[c] = pd.to_numeric(display_df[c], errors='coerce')

        display_df = display_df.sort_values(by='Total Gain %', ascending=False)
        
        st.dataframe(
            display_df.style.format({
                "Current Price": "${:.2f}", "Market Value": "${:,.0f}",
                "52W Low": "${:.2f}", "52W High": "${:.2f}", "Market Cap": "${:,.0f}",
                "Day Change %": "{:+.2f}%", "30D %": "{:+.2f}%", "1Y %": "{:+.2f}%", "Total Gain %": "{:+.2f}%", 
                "Analyst Upside": "{:+.2f}%", "Div Yield %": "{:.2f}%", "P/E Ratio": "{:.1f}",
                "Vol Ratio": "{:.1f}x", "Daily Liquidity": "${:,.0f}"
            }, na_rep="-")
            .background_gradient(subset=['Total Gain %'], cmap="RdYlGn", vmin=-50, vmax=50)
            .background_gradient(subset=['Analyst Upside'], cmap="RdYlGn", vmin=-10, vmax=30)
            .background_gradient(subset=['Day Change %'], cmap="RdYlGn", vmin=-3, vmax=3)
            .background_gradient(subset=['30D %'], cmap="RdYlGn", vmin=-5, vmax=5)
            .background_gradient(subset=['1Y %'], cmap="RdYlGn", vmin=-15, vmax=15)
            .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8)
            .background_gradient(subset=['Vol Ratio'], cmap="Reds", vmin=0.5, vmax=2.5),
            column_config={
                "Ticker": st.column_config.LinkColumn(
                    "Ticker", display_text=r"https://finance\.yahoo\.com/quote/(.*)"
                ),
                "URL": None,
                "Vol Ratio": st.column_config.NumberColumn("Vol Ratio", help="Relative Volume (1.0 = Normal)"),
                "Daily Liquidity": st.column_config.NumberColumn("Liquidity", help="Avg Daily Volume x Price")
            },
            use_container_width=True, height=600
        )
        
        # PIE CHARTS
        st.markdown("---")
        st.subheader("📊 Portfolio Composition")
        c_sector, c_stock = st.columns(2)
        with c_sector:
            st.caption("By Sector")
            if 'Sector' in portfolio.columns:
                sector_group = portfolio.groupby('Sector')['Market Value'].sum()
                fig, ax = plt.subplots(figsize=(5, 5))
                fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
                colors = plt.cm.Paired(np.linspace(0, 1, len(sector_group)))
                ax.pie(sector_group, labels=sector_group.index, autopct='%1.0f%%', pctdistance=0.8, startangle=90, colors=colors, textprops={'color':"white"})
                fig.gca().add_artist(plt.Circle((0,0),0.60,fc='#0E1117'))
                st.pyplot(fig)

        with c_stock:
            st.caption("By Stock")
            if 'Ticker' in portfolio.columns:
                stock_group = portfolio.groupby('Ticker')['Market Value'].sum().sort_values(ascending=False)
                fig2, ax2 = plt.subplots(figsize=(5, 5))
                fig2.patch.set_facecolor('#0E1117'); ax2.set_facecolor('#0E1117')
                colors2 = plt.cm.tab20c(np.linspace(0, 1, len(stock_group)))
                total_val = stock_group.sum()
                labels = [idx if (val/total_val > 0.02) else '' for idx, val in zip(stock_group.index, stock_group)]
                ax2.pie(stock_group, labels=labels, autopct=lambda p: f'{p:.0f}%' if p > 2 else '', pctdistance=0.8, startangle=90, colors=colors2, textprops={'color':"white"})
                fig2.gca().add_artist(plt.Circle((0,0),0.60,fc='#0E1117'))
                st.pyplot(fig2)

        # BAR CHART
        st.markdown("---")
        st.subheader("🚀 Total Return by Stock")
        perf_df = portfolio.sort_values(by='Total Gain %', ascending=False)
        fig3, ax3 = plt.subplots(figsize=(12, 5))
        fig3.patch.set_facecolor('#0E1117'); ax3.set_facecolor('#0E1117')
        colors_bar = ['#00FF00' if x >= 0 else '#FF0000' for x in perf_df['Total Gain %']]
        bars = ax3.bar(perf_df['Ticker'], perf_df['Total Gain %'], color=colors_bar)
        ax3.set_ylabel("Total Gain %", color="white"); ax3.tick_params(colors='white')
        ax3.spines['bottom'].set_color('white'); ax3.spines['left'].set_color('white') 
        ax3.spines['top'].set_visible(False); ax3.spines['right'].set_visible(False)
        for bar in bars:
            height = bar.get_height()
            label_y = height if height > 0 else height - 5
            ax3.text(bar.get_x() + bar.get_width()/2., label_y, f'{height:.0f}%', ha='center', va='bottom' if height > 0 else 'top', color='white', fontsize=9, fontweight='bold')
        st.pyplot(fig3)

    with tab2:
        try:
            h_df = pd.DataFrame(history_sheet.get_all_values()[1:], columns=['Date', 'Value'])
            h_df['Date'] = pd.to_datetime(h_df['Date']); h_df['Value'] = pd.to_numeric(h_df['Value'])
            st.area_chart(h_df.set_index('Date')['Value'], color="#00FF00")
        except: st.info("No history yet.")