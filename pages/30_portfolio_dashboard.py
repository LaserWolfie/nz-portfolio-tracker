from src.data.sheets import ensure_data_loaded
ensure_data_loaded()

import streamlit as st
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from modules import utils

st.set_page_config(page_title="NZ Wealth Manager Pro — Portfolio Dashboard", page_icon="📊", layout="wide")

# Helper for numbers
def clean_number(x):
    return utils.clean_number(x)

df = st.session_state.prop_df.copy()
personal_df = st.session_state.get('personal_df', pd.DataFrame())
stock_df = st.session_state.get('stock_df', pd.DataFrame())
df.columns = [c.replace(' ', '_') for c in df.columns]

# Data Cleaning for Metrics
df['Current_Value'] = df['Current_Value'].apply(clean_number)
df['Original_Value'] = df['Original_Value'].apply(clean_number)
df['Annual_Distribution'] = df['Annual_Distribution'].apply(clean_number)
if "Last_Updated" not in df.columns:
    df["Last_Updated"] = pd.NA

current_date = datetime.now(ZoneInfo("Pacific/Auckland")).date()
quarter_start_month = ((current_date.month - 1) // 3) * 3 + 1
quarter_start = datetime(current_date.year, quarter_start_month, 1).date()
last_updated = pd.to_datetime(df["Last_Updated"], errors="coerce").dt.date
df["Updated_This_Quarter"] = last_updated >= quarter_start

st.title("📊 NZ Wealth Manager Pro — Portfolio Dashboard")

# --- PORTFOLIO SEGMENTATION ---
parents_df = df[df['Owner_Entity'] != 'Gold Recovery Ltd']
bryn_df = df[df['Owner_Entity'] == 'Gold Recovery Ltd']

# Inter-entity loan cashflow adjustment (Gold Recovery Ltd <-> Group Reality Ltd)
loan_expiry = datetime(2027, 1, 31)
loan_active = datetime.now() < loan_expiry
loan_monthly_amt = 1000
loan_annual_amt = loan_monthly_amt * 12
pension_annual_amt = 38145

# Mum & Dad Metrics
p_assets = parents_df['Current_Value'].sum()
p_stock = 0
if not stock_df.empty:
    stock_df = stock_df.copy()
    if 'Value' in stock_df.columns:
        stock_df['Value'] = stock_df['Value'].apply(clean_number)
        # Exclude any total rows if present
        if 'Ticker' in stock_df.columns:
            stock_df = stock_df[
                ~stock_df['Ticker'].astype(str).str.contains('total', case=False, na=False)
            ]
        p_stock = stock_df['Value'].sum()
p_personal_assets = 0
if not personal_df.empty and 'Current_Value' in personal_df.columns:
    personal_df_assets = personal_df.copy()
    personal_df_assets['Current_Value'] = personal_df_assets['Current_Value'].apply(clean_number)
    if 'Loan_Balance' in personal_df_assets.columns:
        personal_df_assets['Loan_Balance'] = personal_df_assets['Loan_Balance'].apply(clean_number)
    if 'Owner_Entity' in personal_df_assets.columns:
        personal_df_assets = personal_df_assets[personal_df_assets['Owner_Entity'] != 'Gold Recovery Ltd']
    p_personal_assets = personal_df_assets['Current_Value'].sum()
p_personal_debt = personal_df_assets.get('Loan_Balance', 0).sum() if not personal_df.empty else 0
p_assets = p_assets + p_stock + p_personal_assets - p_personal_debt
p_income = parents_df['Annual_Distribution'].sum()
p_cost = parents_df['Original_Value'].sum()
p_yield = (p_income / p_cost * 100) if p_cost > 0 else 0
p_cashflow = p_income + loan_annual_amt + pension_annual_amt if loan_active else p_income + pension_annual_amt

st.markdown("### 👨‍👩‍👧‍👦 Mum & Dad Portfolio")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Assets", f"${p_assets:,.0f}")
k2.metric("Net Cashflow", f"${p_cashflow:,.0f}", help="Includes inter-entity loan cashflow ($1,000/mo) with expiry 2027-01-31")
k3.metric("Property Yield", f"{p_yield:.2f}%")
k4.metric("Status", "Active")

st.markdown("---")

# Bryn Metrics
b_assets = bryn_df['Current_Value'].sum()
b_income = bryn_df['Annual_Distribution'].sum()
b_cost = bryn_df['Original_Value'].sum()
b_yield = (b_income / b_cost * 100) if b_cost > 0 else 0
b_cashflow = b_income - loan_annual_amt if loan_active else b_income

st.markdown("### 👤 Bryn Wilson Portfolio")
kb1, kb2, kb3, kb4 = st.columns(4)
kb1.metric("Assets", f"${b_assets:,.0f}")
kb2.metric("Net Cashflow", f"${b_cashflow:,.0f}", help="Excludes loan outflow ($1,000/mo) with expiry 2027-01-31")
kb3.metric("Property Yield", f"{b_yield:.2f}%")

st.markdown("---")

# --- PERSONAL ASSETS ---
if not personal_df.empty:
    personal_df = personal_df.copy()
    if 'Current_Value' in personal_df.columns:
        personal_df['Current_Value'] = personal_df['Current_Value'].apply(clean_number)
    if 'Loan_Balance' in personal_df.columns:
        personal_df['Loan_Balance'] = personal_df['Loan_Balance'].apply(clean_number)
    if 'Debt_Facility' in personal_df.columns:
        personal_df['Debt_Facility'] = personal_df['Debt_Facility'].apply(clean_number)
    if 'Loan_Rate' in personal_df.columns:
        personal_df['Loan_Rate'] = personal_df['Loan_Rate'].apply(clean_number)
    st.subheader("Personal Assets")
    p_total = personal_df['Current_Value'].sum() if 'Current_Value' in personal_df.columns else 0
    p_debt = personal_df.get('Loan_Balance', 0).sum()
    p_facility = personal_df.get('Debt_Facility', 0).sum()
    cpa1, cpa2, cpa3, cpa4 = st.columns(4)
    cpa1.metric("Personal Assets Total", f"${p_total:,.0f}")
    cpa2.metric("Personal Liabilities Total", f"${p_debt:,.0f}")
    cpa3.metric("Available Debt Facility", f"${p_facility:,.0f}")
    cpa4.metric("Pension Income (Annual)", f"${pension_annual_amt:,.0f}")
    display_cols = [c for c in ['Asset_Name', 'Owner_Entity', 'Address', 'Current_Value', 'Loan_Balance', 'Loan_Rate', 'Debt_Facility', 'Source', 'OneRoof_URL'] if c in personal_df.columns]
    if display_cols:
        column_config = {
            "Current_Value": st.column_config.NumberColumn(format="$%d"),
            "Loan_Balance": st.column_config.NumberColumn(format="$%d"),
            "Debt_Facility": st.column_config.NumberColumn(format="$%d"),
            "Loan_Rate": st.column_config.NumberColumn(format="%.2f")
        }
        if "OneRoof_URL" in display_cols:
            column_config["OneRoof_URL"] = st.column_config.LinkColumn(
                "OneRoof Link",
                display_text="View"
            )
        st.dataframe(
            personal_df[display_cols],
            use_container_width=True,
            column_config=column_config
        )

# --- SYNDICATE DETAILS ---
st.subheader("🔎 Syndicate Details")
display_cols = [
    'Entity_Name',
    'Owner_Entity',
    'Original_Value',
    'Current_Value',
    'Annual_Distribution',
    'Updated_This_Quarter',
]

def _highlight_updated(val):
    if pd.isna(val):
        return ""
    color = "#2e7d32" if bool(val) else "#c62828"
    return f"background-color: {color}; color: white;"

def _format_updated(val):
    if pd.isna(val):
        return ""
    return "Yes" if bool(val) else "No"

display_df = df[display_cols].rename(columns={"Updated_This_Quarter": "Updated (This Qtr)"})
st.dataframe(
    display_df.style.format(
        {
            "Original_Value": "${:,.0f}",
            "Current_Value": "${:,.0f}",
            "Annual_Distribution": "${:,.0f}",
            "Updated (This Qtr)": _format_updated,
        }
    ).applymap(_highlight_updated, subset=["Updated (This Qtr)"]),
    use_container_width=True,
)
