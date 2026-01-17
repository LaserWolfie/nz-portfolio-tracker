import streamlit as st
import os
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from src.data.sheets import ensure_data_loaded, clear_app_caches

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(page_title="NZ Wealth Manager Pro", page_icon="💰", layout="wide")
ensure_data_loaded()

# --- 2. CONNECTION ENGINE ---
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if os.path.exists("credentials.json"):
        return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    elif "gcp_service_account" in st.secrets:
        return ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    else:
        st.error("🚨 Connection Failed: No credentials found.")
        st.stop()

def robust_numeric_clean(df, column_name):
    """Safely converts a spreadsheet column to numbers, handling symbols and errors."""
    df = df.copy()
    if column_name in df.columns:
        clean_col = (
            df[column_name].astype(str)
            .str.replace(r'[$,%]', '', regex=True)
            .str.replace(',', '')
            .str.strip()
            .replace(['#N/A', '#VALUE!', '#DIV/0!', 'None', 'nan', '', '-'], '0')
        )
        df.loc[:, column_name] = pd.to_numeric(clean_col, errors='coerce').fillna(0)
    return df

@st.cache_data(ttl=600)
def load_data_from_sheet():
    try:
        creds = init_connection()
        client = gspread.authorize(creds)
        
        # --- SHARE PORTFOLIO ---
        STOCKS_ID = "1_Fj4lKv2esBxwwVn-ALfHNJp3OGNUChe8lQ5zfMkzD0"
        sheet1 = client.open_by_key(STOCKS_ID)
        s_values = sheet1.worksheet("Clean_Stocks").get_all_values()
        stock_headers = [str(h).strip() for h in s_values[0]]
        stock_df = pd.DataFrame(s_values[1:], columns=stock_headers)
        if 'Value' in stock_df.columns:
            stock_df = robust_numeric_clean(stock_df, 'Value')
        
        # --- PROPORTIONAL PROPERTY ---
        PROPERTY_ID = "142q0VXqiC6RWSjcS67BGR_ROVLYFtl61QgmRrYhoUkQ"
        sheet2 = client.open_by_key(PROPERTY_ID) 
        p_values = sheet2.worksheet("Syndicate_Data").get_all_values()
        prop_headers = [str(h).strip() for h in p_values[0]]
        prop_df = pd.DataFrame(p_values[1:], columns=prop_headers)
        
        # Clean numeric columns for calculations (handle both spaced and underscored headers)
        if 'Current Value' in prop_df.columns:
            prop_df = robust_numeric_clean(prop_df, 'Current Value')
        if 'Current_Value' in prop_df.columns:
            prop_df = robust_numeric_clean(prop_df, 'Current_Value')
        if 'Original Value' in prop_df.columns:
            prop_df = robust_numeric_clean(prop_df, 'Original Value')
        if 'Original_Value' in prop_df.columns:
            prop_df = robust_numeric_clean(prop_df, 'Original_Value')
        if 'Annual_Distribution' in prop_df.columns:
            prop_df = robust_numeric_clean(prop_df, 'Annual_Distribution')

        return stock_df, prop_df
    except Exception as e:
        st.error(f"Sync Error: {e}")
        return pd.DataFrame(), pd.DataFrame()

@st.cache_data(ttl=600)
def load_personal_assets():
    if os.path.exists("personal_assets.csv"):
        df = pd.read_csv("personal_assets.csv")
        if 'Current_Value' in df.columns:
            df = robust_numeric_clean(df, 'Current_Value')
        return df
    return pd.DataFrame()

# --- 3. SIDEBAR & REFRESH ---
st.sidebar.title("⚙️ Controls")
if st.sidebar.button("Refresh Data"):
    clear_app_caches()
    st.rerun()
debug_show_dataframes = st.sidebar.checkbox("Debug: show dataframes")

# --- 4. DATA ORCHESTRATION (THE BRIDGE) ---
stock_df = st.session_state.stock_df
prop_df = st.session_state.prop_df
personal_df = st.session_state.personal_df

if debug_show_dataframes:
    st.write("stock_df shape:", stock_df.shape)
    st.write("stock_df columns:", list(stock_df.columns))
    st.dataframe(stock_df.head(20))

# --- 5. DASHBOARD SUMMARY ---
st.title("💰 NZ Wealth Manager Pro")
st.subheader("Total Wilson Family Assets and Income")

if not stock_df.empty:
    st.success("✨ Data successfully loaded and shared across pages.")
    
    stock_df_for_total = stock_df
    if 'Ticker' in stock_df_for_total.columns:
        stock_df_for_total = stock_df_for_total[
            ~stock_df_for_total['Ticker'].astype(str).str.contains('total', case=False, na=False)
        ]
    t_stock = 0
    for col in ["Market Value", "Market_Value", "Current Value", "Current_Value", "Value"]:
        if col in stock_df_for_total.columns:
            stock_df_for_total = robust_numeric_clean(stock_df_for_total, col)
            t_stock = stock_df_for_total[col].sum()
            break
    t_stock = float(st.session_state.get("stock_assets_total", t_stock) or 0)
    prop_value_series = prop_df.get('Current_Value')
    if prop_value_series is None:
        prop_value_series = prop_df.get('Current Value', 0)
    t_prop = pd.to_numeric(prop_value_series, errors='coerce').sum()
    t_personal = pd.to_numeric(personal_df.get('Current_Value', 0), errors='coerce').sum() if not personal_df.empty else 0
    t_personal_debt = pd.to_numeric(personal_df.get('Loan_Balance', 0), errors='coerce').sum() if not personal_df.empty else 0

    # Total income = stock dividends + property distributions
    stock_income = 0
    if 'Value' in stock_df_for_total.columns and 'Div Yield' in stock_df_for_total.columns:
        vals = pd.to_numeric(stock_df_for_total['Value'], errors='coerce').fillna(0)
        yields = pd.to_numeric(stock_df_for_total['Div Yield'], errors='coerce').fillna(0)
        yields = yields.apply(lambda v: v / 100 if v > 1 else v)
        stock_income = (vals * yields).sum()
    prop_income = pd.to_numeric(prop_df.get('Annual_Distribution', 0), errors='coerce').sum() if not prop_df.empty else 0
    pension_income = 38145
    t_income = stock_income + prop_income + pension_income
    
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Stock Assets", f"${t_stock:,.0f}")
    c2.metric("Property Assets", f"${t_prop:,.0f}")
    c3.metric("Personal Assets", f"${t_personal:,.0f}")
    c4.metric("Total Net Worth", f"${(t_stock + t_prop + t_personal - t_personal_debt):,.0f}")
    c5.metric("Total Income", f"${t_income:,.0f}")
    
    with st.expander("🕵️ Connection Forensics"):
        st.markdown("**Stock Data**")
        st.write("shape:", stock_df.shape)
        st.write("columns:", list(stock_df.columns))
        if {"Ticker", "Value"}.issubset(stock_df.columns):
            st.dataframe(stock_df[["Ticker", "Value"]].head(20))
        else:
            st.dataframe(stock_df.head(20))
        if "Value" in stock_df.columns:
            value_series = pd.to_numeric(stock_df["Value"], errors="coerce").fillna(0)
            st.write("t_stock:", float(value_series.sum()))
            st.write("Value > 0 rows:", int((value_series > 0).sum()))
        else:
            st.write("t_stock:", 0)
            st.write("Value > 0 rows:", 0)
        st.write(f"**Property Columns Found:** {', '.join(prop_df.columns)}")
        st.write(f"**Broadway Data Check:** {'Original_Value' in prop_df.columns}")
else:
    st.warning("⚠️ Waiting for data connection...")
