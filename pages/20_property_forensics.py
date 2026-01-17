from src.data.sheets import ensure_data_loaded
ensure_data_loaded()

import streamlit as st
import pandas as pd
import plotly.express as px
import altair as alt
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
from modules import utils

# --- CONFIGURATION ---
st.set_page_config(page_title="NZ Wealth Manager Pro — Property Forensics", page_icon="🏢", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
<style>
    .metric-card { background-color: #1E1E1E; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    .stMetric { background-color: transparent !important; }
</style>
""", unsafe_allow_html=True)

# --- 1. HELPER FUNCTIONS ---
def clean_number(x):
    return utils.clean_number(x)

def clean_percent(x):
    return utils.clean_percent(x)

def save_to_google_sheet(data_dict):
    """Writes the extracted data to the Google Sheet with EXACT column mapping."""
    try:
        # Load Google Sheets Credentials
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        if "gcp_service_account" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
        client = gspread.authorize(creds)
        
        # --- THE FIX: Correct File and Tab Name ---
        # We use "Proportional Property" (File) and "Syndicate_Data" (Tab)
        sheet = client.open("Proportional Property").worksheet("Syndicate_Data")
        
# --- DYNAMIC ROW MAPPING (Fixed to prevent $0 values) ---
        row = [
            data_dict.get('Entity_Name', ''),           # A
            data_dict.get('Owner_Entity', 'Other'),     # B
            data_dict.get('Manager', 'Unknown'),        # C
            data_dict.get('Original_Value', 0),         # D: Fixed (Retrieves from AI)
            data_dict.get('Current_Value', 0),          # E
            data_dict.get('Original_Distribution', 0),  # F: Fixed (Retrieves from AI)
            data_dict.get('Annual_Distribution', 0),    # G
            data_dict.get('LVR_Percent', 0),            # H
            data_dict.get('WALT_Years', 0),             # I
            data_dict.get('Vacancy_Percent', 0),        # J
            "",                                         # K
            "",                                         # L
            "",                                         # M
            data_dict.get('Distribution_At_Risk', 'No'),# N
            data_dict.get('Capital_Raise', 0),          # O
            data_dict.get('Capex_Planned', 0),          # P
            data_dict.get('Expense_Ratio', 0),          # Q
            data_dict.get('Debt_Yield', 0),             # R
            data_dict.get('CapEx_Reserves', 0),         # S
            data_dict.get('Loan_Expiry_Year', ''),      # T
            data_dict.get('Sector', 'Other'),           # U
            data_dict.get('Interest_Cover', 0)          # V
        ]
        
        # --- SAFETY SAVE ---
        try:
            sheet.append_row(row)
            return True
        except Exception as e:
            # If Google sends a "200 OK" but the old library thinks it's an error
            if "200" in str(e):
                return True
            else:
                raise e

    except Exception as e:
        st.error(f"Save Error: {e}")
        return False
        
        # --- SAFETY SAVE ---
        try:
            sheet.append_row(row)
            return True
        except Exception as e:
            # If Google sends a "200 OK" but the old library thinks it's an error
            if "200" in str(e):
                return True
            else:
                raise e

    except Exception as e:
        st.error(f"Save Error: {e}")
        return False
        
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"Save Error: {e}")
        return False

# --- DATA LOADING ---
df = st.session_state.prop_df.copy()
df.columns = [c.replace(' ', '_') for c in df.columns]

# Remove Totals
df = df[~df['Entity_Name'].astype(str).str.lower().str.contains('total', na=False)]

# --- STANDARD CLEANING ---
df['Current_Value'] = df['Current_Value'].apply(clean_number)
df['Original_Value'] = df['Original_Value'].apply(clean_number)
df['Annual_Distribution'] = df['Annual_Distribution'].apply(clean_number)
df['LVR_Percent'] = df['LVR_Percent'].apply(clean_percent)

# --- ADVANCED COLUMNS ---
adv_cols = {
    'WALT_Years': 0.0, 'Interest_Cover': 0.0, 'Vacancy_Percent': 0.0, 
    'Expense_Ratio': 0.0, 'Debt_Yield': 0.0, 'CapEx_Reserves': 0.0,
    'Distribution_At_Risk': 'No', 'Capital_Raise': 0.0, 'Capex_Planned': 0.0,
    'Loan_Expiry_Year': 0, 'Sector': 'Other'
}

for col, default in adv_cols.items():
    if col not in df.columns: df[col] = default
    else:
        if isinstance(default, float):
            if 'Percent' in col or 'Ratio' in col or 'Yield' in col: df[col] = df[col].apply(clean_percent)
            else: df[col] = df[col].apply(clean_number)
        elif isinstance(default, int): df[col] = df[col].apply(lambda x: int(clean_number(x)))
        else: df[col] = df[col].fillna('Other').astype(str)

# --- 📉 RATE SCENARIO ENGINE ---
st.sidebar.header("📉 Interest Rate Scenario")
rate_adjustment = st.sidebar.slider("Rate Adjustment (+/-%)", -2.0, 5.0, 0.0, 0.25, format="%+.2f%%")
yield_threshold = st.sidebar.slider("Yield Alert Threshold (%)", 0.0, 10.0, 5.0, 0.25)

# Calcs
df['Debt_Value'] = df['Current_Value'] * df['LVR_Percent']
df['Rate_Impact_Cost'] = df['Debt_Value'] * (rate_adjustment / 100)
df['Scenario_Distribution'] = df['Annual_Distribution'] - df['Rate_Impact_Cost']
df['Scenario_Yield'] = (df['Scenario_Distribution'] / df['Original_Value'] * 100).fillna(0)
scenario_label = f"{rate_adjustment:+.2f}% Rates" if rate_adjustment != 0 else "Current Rates"

# --- DASHBOARD LAYOUT ---
st.title("🏢 NZ Wealth Manager Pro — Property Forensics")

# Create Tabs to separate View vs Input
tab_dash, tab_upload = st.tabs(["📊 Portfolio Dashboard", "📄 Upload Report (AI Scanner)"])

# ==========================================
# TAB 1: FORENSIC DASHBOARD
# ==========================================
with tab_dash:
    # --- 1. PORTFOLIO SEGMENTATION & METRICS ---
    parents_df = df[df['Owner_Entity'] != 'Gold Recovery Ltd']
    bryn_df = df[df['Owner_Entity'] == 'Gold Recovery Ltd']

    # Mum & Dad Metrics
    p_assets = parents_df['Current_Value'].sum()
    p_income_pure = parents_df['Scenario_Distribution'].sum()
    p_cost = parents_df['Original_Value'].sum()
    p_yield = (p_income_pure / p_cost * 100) if p_cost > 0 else 0

    # Inter-entity loan cashflow adjustment (Gold Recovery Ltd <-> Group Reality Ltd)
    loan_expiry = datetime(2027, 1, 31)
    loan_active = datetime.now() < loan_expiry
    loan_monthly_amt = 1000
    loan_annual_amt = loan_monthly_amt * 12
    p_cashflow = p_income_pure + loan_annual_amt if loan_active else p_income_pure

    st.markdown("### 👨‍👩‍👧‍👦 Mum & Dad Portfolio")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Proportional Property Assets", f"${p_assets:,.0f}")
    k2.metric(
        "Net Cashflow",
        f"${p_cashflow:,.0f}",
        help="Includes inter-entity loan cashflow ($1,000/mo) with expiry 2027-01-31"
    )
    k3.metric("Property Yield", f"{p_yield:.2f}%")
    k4.metric("Loan Status", "Active" if loan_active else "Expired")

    st.markdown("---")

    # Bryn Metrics
    b_assets = bryn_df['Current_Value'].sum()
    b_income_pure = bryn_df['Scenario_Distribution'].sum()
    b_cost = bryn_df['Original_Value'].sum()
    b_cashflow = b_income_pure - loan_annual_amt if loan_active else b_income_pure
    b_yield = (b_income_pure / b_cost * 100) if b_cost > 0 else 0

    st.markdown("### 👤 Bryn Wilson Portfolio")
    kb1, kb2, kb3, kb4 = st.columns(4)
    kb1.metric("Proportional Property Assets", f"${b_assets:,.0f}")
    kb2.metric(
        "Net Cashflow",
        f"${b_cashflow:,.0f}",
        help="Excludes loan outflow ($1,000/mo) with expiry 2027-01-31"
    )
    kb3.metric("Property Yield", f"{b_yield:.2f}%")
    kb4.metric("Scenario", scenario_label)

    st.markdown("---")

    # --- 2. RISK RADAR & REFINANCING ---
    st.subheader("🕵️‍♂️ Risk & Refinancing")
    rt1, rt2, rt3, rt4 = st.tabs(["⚠️ Core Risks", "💸 Funding", "🏦 Refinancing & Sector", "🔬 Advanced"])

    with rt1:
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            st.markdown("##### Leverage (LVR)")
            high_lvr = df[df['LVR_Percent'] > 0.45].sort_values('LVR_Percent', ascending=False)
            if not high_lvr.empty:
                for _, r in high_lvr.iterrows():
                    st.error(f"**{r['Entity_Name']}**: High LVR {(r['LVR_Percent']*100):.1f}%")
            else:
                st.success("?o. LVR Safe (<45%)")

        with col_r2:
            st.markdown("##### Yield vs Cost of Debt")
            low_yield = df[df['Scenario_Yield'] <= yield_threshold].sort_values('Scenario_Yield', ascending=True)
            if not low_yield.empty:
                for _, r in low_yield.iterrows():
                    st.warning(f"**{r['Entity_Name']}**: Low Yield {r['Scenario_Yield']:.2f}% (<= {yield_threshold:.2f}%)")
            else:
                st.success(f"No yields below {yield_threshold:.2f}%")
    with rt2:
        c_dist, c_cap, c_capex = st.columns(3)
        with c_dist:
            st.markdown("##### 🚨 Distribution At Risk")
            at_risk = df[df['Distribution_At_Risk'].astype(str).str.lower().isin(['yes', 'high', 'true', '1'])]
            if not at_risk.empty:
                for _, r in at_risk.iterrows(): st.error(f"**{r['Entity_Name']}**: ⛔ Distributions Halted/Risked")
            else: st.success("✅ No distribution risks.")
        with c_capex:
            st.markdown("##### 🏗️ CapEx Funding Gap")
            capex_active = df[(df['Capex_Planned'] > 0) | (df['CapEx_Reserves'] > 0)].copy()
            if not capex_active.empty:
                capex_active['Shortfall'] = capex_active['Capex_Planned'] - capex_active['CapEx_Reserves']
                shortfall = capex_active[capex_active['Shortfall'] > 0]
                if not shortfall.empty:
                    for _, r in shortfall.iterrows(): st.error(f"**{r['Entity_Name']}**: Gap ${r['Shortfall']:,.0f}")
                else: st.success("✅ CapEx fully funded.")
            else: st.info("No CapEx data.")

    with rt3:
        # Define columns here to prevent NameError
        c_refi, c_sec = st.columns(2)
        with c_refi:
            st.markdown("##### 🏛️ Debt Maturity Wall")
            if 'Loan_Expiry_Year' in df.columns and df['Loan_Expiry_Year'].sum() > 0:
                maturity_chart_tab = alt.Chart(df[df['Loan_Expiry_Year'] > 0]).mark_bar().encode(
                    x=alt.X('Loan_Expiry_Year:O', title='Expiry Year'),
                    y=alt.Y('sum(Current_Value):Q', title='Exposure ($)'),
                    color=alt.Color('Entity_Name:N', title='Property'),
                    tooltip=[alt.Tooltip('Entity_Name'), alt.Tooltip('Loan_Expiry_Year'), alt.Tooltip('Current_Value', format='$,.0f')]
                ).properties(height=300).interactive()
                st.altair_chart(maturity_chart_tab, use_container_width=True)
            else: st.info("No expiry data found.")

        with c_sec:
            st.markdown("##### 🏗️ Sector Exposure")
            if 'Sector' in df.columns and not df.empty:
                fig_sec = px.pie(df, values='Current_Value', names='Sector', hole=0.4)
                fig_sec.update_layout(showlegend=True, height=300, margin=dict(t=0, b=0, l=0, r=0))
                st.plotly_chart(fig_sec, use_container_width=True)

    with rt4:
        st.markdown("**Detailed Asset Forensics**")
        st.write(df[['Entity_Name', 'Interest_Cover', 'Vacancy_Percent', 'Expense_Ratio']])

    st.markdown("---")

    # --- 3. DETAILED TABLE ---
    st.subheader(f"🔎 Syndicate Details ({scenario_label})")
    display_cols = ['Entity_Name', 'Owner_Entity', 'Original_Value', 'Annual_Distribution', 'Scenario_Distribution', 'Scenario_Yield', 'LVR_Percent']
    st.dataframe(df[display_cols].style.format({"Original_Value": "${:,.0f}", "Annual_Distribution": "${:,.0f}", "Scenario_Distribution": "${:,.0f}"}), use_container_width=True)
# ==========================================
# TAB 2: AI REPORT SCANNER
# ==========================================
with tab_upload:
    st.header("📄 PDF Report Scanner")
    st.markdown("Upload an Annual Report PDF. Gemini AI will extract the forensic data for you.")
    
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🔑 API Key loaded from secrets")
    else:
        api_key = st.text_input("Enter Gemini API Key:", type="password")
    
    uploaded_file = st.file_uploader("Drag & Drop Report Here", type=['pdf'])
    
    if uploaded_file and api_key:
        if st.button("🚀 Scan Document", type="primary"):
            with st.spinner("🤖 AI Analyst is reading the report..."):
                try:
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.0-flash')
                    pdf_data = uploaded_file.read()
                    
                    prompt = "Extract forensic property metrics from this PDF and return as JSON."
                    response = model.generate_content([{'mime_type': 'application/pdf', 'data': pdf_data}, prompt])
                    cleaned_text = response.text.replace('```json', '').replace('```', '').strip()
                    st.session_state['scanned_data'] = json.loads(cleaned_text)
                    st.success("✅ Extraction Complete!")
                except Exception as e:
                    st.error(f"AI Error: {e}")
