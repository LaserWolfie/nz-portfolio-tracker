import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="NZ Wealth Manager Pro",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLE OVERRIDES ---
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; }
    div.stButton > button { width: 100%; }
</style>
""", unsafe_allow_html=True)

# --- SIDEBAR: PRO SETTINGS ---
st.sidebar.title("⚙️ Pro Settings")

# 1. Dynamic Tax Bracket Selector
st.sidebar.subheader("Tax Parameters")
tax_brackets = {
    "10.5% ($0 - $15.6k)": 0.105,
    "17.5% ($15.6k - $53.5k)": 0.175,
    "30.0% ($53.5k - $78.1k)": 0.30,
    "33.0% ($78.1k - $180k)": 0.33,
    "39.0% ($180k+)": 0.39
}

selected_label = st.sidebar.selectbox(
    "Select Marginal Tax Rate:",
    options=list(tax_brackets.keys()),
    index=1, # Defaults to 17.5%
    help="Select your personal income bracket to recalculate tax arbitrage."
)
MARGINAL_TAX_RATE = tax_brackets[selected_label]

# 2. Mortgage Parameters (Now Interactive too)
st.sidebar.subheader("Debt Parameters")
MORTGAGE_RATE = st.sidebar.number_input("Mortgage Interest Rate (%)", min_value=0.0, max_value=15.0, value=5.0, step=0.1) / 100
MORTGAGE_DRAWN = st.sidebar.number_input("Mortgage Balance ($)", value=430000, step=1000)
LIQUIDITY_AVAILABLE = st.sidebar.number_input("Available Liquidity ($)", value=100000, step=1000)

# Constants
COMPANY_TAX_RATE = 0.28     # 28% Imputation Rate

# --- HELPER FUNCTIONS ---
def get_google_sheet():
    """Connects to Google Sheets using secrets."""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        client = gspread.authorize(creds)
        return client.open("Share Portfolio")
    except Exception as e:
        st.error(f"🔌 Connection failed: {e}")
        st.stop()

def clean_val(x):
    """Converts currency strings '$1,234.56' to floats 1234.56"""
    if pd.isna(x) or str(x).strip() == "": return 0.0
    try:
        return float(str(x).replace('$', '').replace(',', '').replace('%', '').replace(' ', '').strip())
    except: return 0.0

@st.cache_data(ttl=300) 
def load_data_pro():
    sheet = get_google_sheet()
    
    # 1. Load Stocks
    try:
        s_data = sheet.worksheet("Share Portfolio").get_all_records()
        stock_df = pd.DataFrame(s_data)
        if 'Market Value' not in stock_df.columns: stock_df['Market Value'] = 0
        if 'Est. Annual Income' not in stock_df.columns: stock_df['Est. Annual Income'] = 0
    except: stock_df = pd.DataFrame()

    # 2. Load Syndicates
    try:
        p_data = sheet.worksheet("Syndicate_Data").get_all_records()
        prop_df = pd.DataFrame(p_data)
        if 'Current_Value' not in prop_df.columns: prop_df['Current_Value'] = 0
        if 'Annual_Distribution' not in prop_df.columns: prop_df['Annual_Distribution'] = 0
    except: prop_df = pd.DataFrame()
    
    return stock_df, prop_df

# --- MAIN DASHBOARD LOGIC ---
st.title("💰 NZ Wealth Manager: Pro Edition")
st.caption(f"Forensic Analysis Mode | Tax Bracket: **{MARGINAL_TAX_RATE*100}%** | Mortgage: **{MORTGAGE_RATE*100}%**")

stock_df, prop_df = load_data_pro()

if not stock_df.empty and not prop_df.empty:
    
    # --- 1. DATA PROCESSING ---
    stock_df['Net_Value'] = stock_df['Market Value'].apply(clean_val)
    stock_df['Net_Income'] = stock_df['Est. Annual Income'].apply(clean_val)
    
    prop_df['Net_Value'] = prop_df['Current_Value'].apply(clean_val)
    prop_df['Net_Income'] = prop_df['Annual_Distribution'].apply(clean_val)
    
    # Totals
    total_stock_val = stock_df['Net_Value'].sum()
    total_prop_val = prop_df['Net_Value'].sum()
    total_wealth = total_stock_val + total_prop_val
    
    total_stock_inc = stock_df['Net_Income'].sum()
    total_prop_inc = prop_df['Net_Income'].sum()
    total_passive_income = total_stock_inc + total_prop_inc
    
    # --- 2. DYNAMIC TAX CALCULATIONS ---
    # Imputation Logic:
    # Gross Dividend = Net / (1 - 0.28)
    # Tax Liability = Gross * User_Selected_Rate
    # Credits Attached = Gross * 0.28
    
    gross_stock_income = total_stock_inc / (1 - COMPANY_TAX_RATE)
    imputation_credits = gross_stock_income * COMPANY_TAX_RATE
    tax_liability = gross_stock_income * MARGINAL_TAX_RATE
    
    # Positive = Refund, Negative = Tax Bill
    imputation_result = imputation_credits - tax_liability
    
    # Mortgage Cost
    annual_mortgage_cost = MORTGAGE_DRAWN * MORTGAGE_RATE
    wealth_gap = total_passive_income - annual_mortgage_cost

    # --- 3. HIGH LEVEL METRICS ---
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Invested Assets", f"${total_wealth:,.0f}", 
                  delta=f"Cash: ${LIQUIDITY_AVAILABLE:,.0f}", delta_color="off")
        
    with col2:
        st.metric("Gross Passive Income", f"${total_passive_income:,.0f}", 
                  help="Combined Dividends & Property Distributions")
        
    with col3:
        st.metric(f"Mortgage Cost ({MORTGAGE_RATE*100}%)", f"-${annual_mortgage_cost:,.0f}", 
                  f"Debt: ${MORTGAGE_DRAWN:,.0f}", delta_color="inverse")
        
    with col4:
        icon = "🟢" if wealth_gap > 0 else "🔴"
        st.metric("The Wealth Gap", f"${wealth_gap:,.0f}", 
                  f"{icon} Surplus/Deficit", delta_color="normal" if wealth_gap > 0 else "inverse")

    st.markdown("---")

    # --- 4. CHARTS ---
    c1, c2 = st.columns([1, 1])
    
    with c1:
        st.subheader("📊 Asset Allocation")
        alloc_data = pd.DataFrame({
            'Asset': ['NZ/AU Stocks', 'Property Syndicates'],
            'Value': [total_stock_val, total_prop_val]
        })
        fig = px.pie(alloc_data, values='Value', names='Asset', hole=0.5, 
                     color_discrete_sequence=['#00CC96', '#636EFA'])
        fig.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=250)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("💵 Income Source")
        inc_data = pd.DataFrame({
            'Source': ['Stocks', 'Property'],
            'Income': [total_stock_inc, total_prop_inc]
        })
        fig2 = px.bar(inc_data, x='Income', y='Source', orientation='h', text_auto='.2s',
                      color='Source', color_discrete_map={'Stocks': '#00CC96', 'Property': '#636EFA'})
        fig2.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=250)
        st.plotly_chart(fig2, use_container_width=True)

    # --- 5. FORENSIC STRATEGY ENGINE ---
    st.subheader("🧠 Forensic Strategy Engine")
    
    blended_yield = (total_passive_income / total_wealth) if total_wealth > 0 else 0
    
    s1, s2, s3 = st.columns(3)
    
    with s1:
        st.markdown("### 📉 Tax Imputation")
        if imputation_result > 0:
            st.success(f"""
            **Refund Opportunity**
            Your rate ({MARGINAL_TAX_RATE*100}%) is lower than the company rate (28%).
            * Credits: **${imputation_credits:,.0f}**
            * Liability: **${tax_liability:,.0f}**
            * **Est. Refund:** **${imputation_result:,.0f}**
            """)
        elif imputation_result < 0:
            st.warning(f"""
            **Tax Top-Up Required**
            Your rate ({MARGINAL_TAX_RATE*100}%) is higher than the company rate (28%).
            * Credits: **${imputation_credits:,.0f}**
            * Liability: **${tax_liability:,.0f}**
            * **Tax Bill:** **${abs(imputation_result):,.0f}**
            """)
        else:
            st.info("Tax Neutral. Your rate matches the imputation rate.")

    with s2:
        st.markdown("### 🏦 Debt Hurdle")
        if blended_yield < MORTGAGE_RATE:
            st.error(f"""
            **Negative Carry Alert**
            Portfolio Yield: **{blended_yield*100:.2f}%**
            Mortgage Cost: **{MORTGAGE_RATE*100:.2f}%**
            * You are losing **${abs(wealth_gap):,.0f}/yr** vs paying down debt.
            """)
        else:
            st.success(f"""
            **Positive Carry**
            Portfolio Yield: **{blended_yield*100:.2f}%**
            Mortgage Cost: **{MORTGAGE_RATE*100:.2f}%**
            * You earn **${wealth_gap:,.0f}/yr** above debt costs.
            """)
            
    with s3:
        st.markdown("### 🏢 Property Scan")
        high_lvr = prop_df[prop_df['LVR_Percent'].apply(clean_val) > 0.45]
        count = len(high_lvr)
        if count > 0:
            st.warning(f"⚠️ **{count} Syndicates** > 45% LVR.")
            st.dataframe(high_lvr[['Entity_Name', 'LVR_Percent']], hide_index=True)
        else:
            st.success("✅ LVR Levels Safe (<45%).")

else:
    st.warning("⚠️ No Data Found. Please ensure Google Sheet tabs are named correctly.")