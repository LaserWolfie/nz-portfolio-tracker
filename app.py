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
    
    # 1. Validate Basic Columns
    required_cols = ['Ticker', 'Shares', 'Purchase Price']
    for col in required_cols:
        if col not in df.columns:
            st.error(f"❌ Missing column: '{col}'. Check your sheet headers.")
            st.stop()
    
    # 2. Validate/Create "Memory" Columns (Matches your screenshot)
    # Mapping: Code Name -> Your Sheet Header Name
    col_map = {
        'Market Cap': 'Market Cap',
        'Analyst Target': 'Analyst Target',
        'P/E Ratio': 'P/E',          # <--- Adjusted to match your "P/E" column
        '52W High': '52W High',
        '52W Low': '52W Low',
        'Sector': 'Sector'
    }

    # Ensure these exist in the DataFrame (even if empty) so code doesn't break
    for internal_name, sheet_header in col_map.items():
        if sheet_header not in df.columns:
            # If your sheet is missing "Analyst Target", we create a placeholder in memory
            df[sheet_header] = "" 

    portfolio = df.copy()
    portfolio = portfolio[portfolio['Ticker'] != '']
    
    # --- ROBUST CLEANING FUNCTIONS ---
    def clean_currency(x):
        if isinstance(x, str):
            return x.replace('$', '').replace(',', '').strip()
        return x

    def clean_financial_text(x):
        """Cleans values like '$7,754 M' or '38 x' into pure numbers"""
        if isinstance(x, (int, float)): return x
        if not isinstance(x, str): return float('nan')
        # Remove M (millions), B (billions), x (times), $ (currency), , (commas)
        s = x.upper().replace(',', '').replace('$', '').replace('M', '').replace('B', '').replace('X', '').strip()
        if s == '' or s == '-': return float('nan')
        try:
            val = float(s)
            # Heuristic: If value is small (e.g. 7754) but header is Market Cap, 
            # it probably meant Millions. However, for safety, we treat as raw first.
            # Yahoo will overwrite this with the full number (7754000000) soon anyway.
            return val
        except:
            return float('nan')

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
    st.info("If deploying to Cloud, ensure you added your secrets in Advanced Settings.")
    st.stop()

# --- MAIN DASHBOARD ---
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

    # --- STEP 2: BULK PRICE HISTORY ---
    with st.spinner('Fetching prices...'):
        try:
            bulk_data = yf.download(ticker_list, period="1y", group_by='ticker', progress=False)
            
            curr_prices, prev_prices, p30_prices, p1y_prices = [], [], [], []
            betas = []
            
            if market_hist_data is not None:
                market_returns = market_hist_data.pct_change().dropna()
            else:
                market_returns = pd.Series([])

            for t in ticker_list:
                try:
                    if len(ticker_list) == 1: df_t = bulk_data
                    else: df_t = bulk_data[t]
                    
                    df_t = df_t.dropna(how='all')
                    
                    if not df_t.empty and 'Close' in df_t.columns:
                        closes = df_t['Close']
                        curr = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2]) if len(closes) >= 2 else curr
                        p30 = float(closes.iloc[-22]) if len(closes) >= 22 else (float(closes.iloc[0]) if len(closes) > 0 else curr)
                        p1y = float(closes.iloc[0]) if len(closes) > 0 else curr
                        
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
                    else:
                        raise ValueError("No data")
                except:
                    curr_prices.append(0.0); prev_prices.append(0.0)
                    p30_prices.append(0.0); p1y_prices.append(0.0); betas.append(1.0)

            portfolio['Current Price'] = curr_prices
            portfolio['Previous Price'] = prev_prices
            portfolio['Price 30d'] = p30_prices
            portfolio['Price 1y'] = p1y_prices
            portfolio['Beta'] = betas
            
        except Exception as e:
            st.error(f"Data Error: {e}"); st.stop()

    # --- STEP 3: FUNDAMENTALS (SMART PERSISTENCE) ---
    progress = st.progress(0); status = st.empty()
    
    final_pe, final_div, final_mcap, final_upside = [], [], [], []
    final_52_lo, final_52_hi = [], []
    
    # Identify Header Names from Map
    h_mcap = col_map['Market Cap']
    h_target = col_map['Analyst Target']
    h_pe = col_map['P/E Ratio']
    h_52h = col_map['52W High']
    h_52l = col_map['52W Low']

    for i, row in portfolio.iterrows():
        t = row['Yahoo_Ticker']
        status.text(f"Analyzing {t}...")
        progress.progress((i+1)/len(portfolio))
        
        # 1. READ EXISTING DATA (Clean it first)
        curr_mcap = clean_financial_text(row.get(h_mcap))
        curr_target = clean_financial_text(row.get(h_target))
        curr_pe = clean_financial_text(row.get(h_pe))
        curr_52h = clean_financial_text(row.get(h_52h))
        curr_52l = clean_financial_text(row.get(h_52l))
        curr_div_pct = 0.0

        # 2. FETCH NEW DATA
        fetched_data = False
        try:
            stock = yf.Ticker(t)
            
            # A. Fast Info
            try:
                if hasattr(stock, 'fast_info'):
                    if stock.fast_info.market_cap: 
                        curr_mcap = stock.fast_info.market_cap; fetched_data = True
                    if stock.fast_info.year_high: 
                        curr_52h = stock.fast_info.year_high; fetched_data = True
                    if stock.fast_info.year_low: 
                        curr_52l = stock.fast_info.year_low; fetched_data = True
            except: pass

            # B. Deep Info
            try:
                info = stock.info
                if info.get('trailingPE'): 
                    curr_pe = info['trailingPE']; fetched_data = True
                if info.get('targetMeanPrice'): 
                    curr_target = info['targetMeanPrice']; fetched_data = True
                
                div = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
                if div: curr_div_pct = div * 100
            except: pass
            
            time.sleep(0.1)
        except: pass

        # 3. SAVE TO SHEET (Update row by row if we found fresh data)
        # Note: We use i+2 because Row 1 is headers
        if fetched_data:
            try:
                # Update Market Cap
                if not pd.isna(curr_mcap) and h_mcap in df.columns:
                    sheet.update_cell(i+2, df.columns.get_loc(h_mcap)+1, curr_mcap)
                # Update Target
                if not pd.isna(curr_target) and h_target in df.columns:
                    sheet.update_cell(i+2, df.columns.get_loc(h_target)+1, curr_target)
                # Update P/E
                if not pd.isna(curr_pe) and h_pe in df.columns:
                    sheet.update_cell(i+2, df.columns.get_loc(h_pe)+1, curr_pe)
                # Update 52W
                if not pd.isna(curr_52h) and h_52h in df.columns:
                    sheet.update_cell(i+2, df.columns.get_loc(h_52h)+1, curr_52h)
                if not pd.isna(curr_52l) and h_52l in df.columns:
                    sheet.update_cell(i+2, df.columns.get_loc(h_52l)+1, curr_52l)
            except: pass

        # 4. STORE FOR DISPLAY
        final_pe.append(curr_pe)
        final_div.append(curr_div_pct)
        final_mcap.append(curr_mcap)
        final_52_hi.append(curr_52h)
        final_52_lo.append(curr_52l)
        
        # Calc Upside
        price_now = portfolio.loc[i, 'Current Price']
        if not pd.isna(curr_target) and price_now > 0:
            upside_val = ((curr_target - price_now) / price_now) * 100
        else:
            upside_val = float('nan')
        final_upside.append(upside_val)

    status.empty(); progress.empty()
    
    portfolio['P/E Ratio'] = final_pe
    portfolio['Div Yield %'] = final_div
    portfolio['Market Cap'] = final_mcap
    portfolio['Analyst Upside'] = final_upside
    portfolio['52W Low'] = final_52_lo
    portfolio['52W High'] = final_52_hi

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
    
    if total_value > 0:
        portfolio['Weight'] = portfolio['Market Value'] / total_value
        portfolio_beta = (portfolio['Weight'] * portfolio['Beta']).sum()
    else: portfolio_beta = 1.0

    if portfolio_beta > 1.15: risk_label = "Aggressive 🚀"; risk_msg = "Growth focus."
    elif portfolio_beta < 0.85: risk_label = "Defensive 🛡️"; risk_msg = "Preservation focus."
    else: risk_label = "Balanced ⚖️"; risk_msg = "Market tracking."

    # --- SAVE HISTORY ---
    today_str = datetime.now().strftime("%Y-%m-%d")
    existing_history = history_sheet.get_all_values()
    if len(existing_history) < 2 or existing_history[-1][0] != today_str:
        history_sheet.append_row([today_str, total_value])
        st.toast(f"✅ Saved today's value: ${total_value:,.2f}")
    else: st.toast("ℹ️ History already up to date.")

    # --- UI LAYOUT ---
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
    
    prev_val = total_value - day_gain_val
    port_day_pct = (day_gain_val / prev_val * 100) if prev_val > 0 else 0
    alpha = port_day_pct - market_return_pct
    b2.metric("Alpha", f"{alpha:+.2f}%", delta=f"Portfolio: {port_day_pct:+.2f}%")
    b3.metric("Beta", f"{portfolio_beta:.2f}")
    b4.metric("Strategy", risk_label)
    st.caption(f"Risk Assessment: {risk_msg}")

    # Tabs
    st.markdown("---")
    tab1, tab2 = st.tabs(["🔎 Holdings Table", "📈 Wealth History"])
    
    with tab1:
        # Table
        display_df = portfolio[['Ticker', 'Market Cap', 'Analyst Upside', 'Current Price', '52W Low', '52W High', 'Day Change %', '30D %', '1Y %', 'Total Gain %', 'P/E Ratio', 'Div Yield %', 'Market Value']].copy()
        display_df = display_df.sort_values(by='Total Gain %', ascending=False)
        
        st.dataframe(
            display_df.style.format({
                "Current Price": "${:.2f}", "Market Value": "${:,.0f}",
                "52W Low": "${:.2f}", "52W High": "${:.2f}", 
                "Day Change %": "{:+.2f}%", "30D %": "{:+.2f}%", 
                "1Y %": "{:+.2f}%", "Total Gain %": "{:+.2f}%", 
                "Beta": "{:.2f}", "Div Yield %": "{:.2f}%", 
                "P/E Ratio": "{:.1f}", "Market Cap": "${:,.0f}",
                "Analyst Upside": "{:+.2f}%"
            }, na_rep="-")
            .background_gradient(subset=['Total Gain %'], cmap="RdYlGn", vmin=-50, vmax=50)
            .background_gradient(subset=['Day Change %'], cmap="RdYlGn", vmin=-5, vmax=5)
            .background_gradient(subset=['Analyst Upside'], cmap="RdYlGn", vmin=-10, vmax=30)
            .background_gradient(subset=['Div Yield %'], cmap="Greens", vmin=0, vmax=8),
            use_container_width=True, height=600
        )

        # Charts
        st.markdown("---")
        st.subheader("📊 Portfolio Composition")
        chart_c1, chart_c2 = st.columns([1, 2])
        
        with chart_c1:
            if 'Sector' in portfolio.columns and not portfolio['Sector'].isnull().all():
                sector_group = portfolio.groupby('Sector')['Market Value'].sum()
                fig, ax = plt.subplots(figsize=(5, 5))
                fig.patch.set_facecolor('#0E1117'); ax.set_facecolor('#0E1117')
                colors = plt.cm.Paired(np.linspace(0, 1, len(sector_group)))
                ax.pie(sector_group, labels=sector_group.index, autopct='%1.0f%%', pctdistance=0.8, startangle=90, colors=colors, textprops={'color':"white"})
                fig.gca().add_artist(plt.Circle((0,0),0.60,fc='#0E1117'))
                st.pyplot(fig)
            else:
                st.warning("Pie Chart unavailable (No Sector data)")
        
        with chart_c2:
            st.info("💡 Tip: The space on this side is now open for adding 'Top Winners' or 'Dividend History' charts in the future!")

    with tab2:
        try:
            fresh_hist = history_sheet.get_all_values()
            if len(fresh_hist) > 1:
                h_df = pd.DataFrame(fresh_hist[1:], columns=fresh_hist[0])
                h_df['Date'] = pd.to_datetime(h_df['Date'])
                h_df['Value'] = pd.to_numeric(h_df['Value'])
                st.subheader("Your Net Worth Journey")
                st.area_chart(h_df.set_index('Date')['Value'], color="#00FF00")
            else: st.info("Not enough history yet.")
        except: st.info("History data unreadable.")