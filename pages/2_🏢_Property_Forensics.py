import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
import plotly.graph_objects as go

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Property Forensics", page_icon="🏢", layout="wide")

# --- SIDEBAR: FORENSIC SETTINGS ---
st.sidebar.header("🕵️ Forensic Settings")

# Dynamic Tax Bracket (Crucial for Arbitrage Math)
tax_options = {
    "10.5%": 0.105,
    "17.5%": 0.175,
    "30.0%": 0.30,
    "33.0%": 0.33,
    "39.0%": 0.39
}
selected_tax = st.sidebar.selectbox("Personal Tax Rate", options=list(tax_options.keys()), index=1)
MARGINAL_TAX_RATE = tax_options[selected_tax]

# Risk Thresholds
LVR_WARNING = st.sidebar.slider("LVR Risk Threshold", 0.30, 0.60, 0.45, 0.05)
WALT_WARNING = st.sidebar.slider("WALT Risk Threshold (Yrs)", 1.0, 5.0, 3.0, 0.5)

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
    """Robust cleaner for currency strings."""
    if pd.isna(x) or str(x).strip() == "": return 0.0
    try:
        s = str(x).replace('$', '').replace(',', '').replace('%', '').replace(' ', '').strip()
        return float(s)
    except: return 0.0

@st.cache_data(ttl=300)
def load_property_data():
    sheet = get_google_sheet()
    try:
        data = sheet.worksheet("Syndicate_Data").get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"Could not load 'Syndicate_Data' tab. Error: {e}")
        return pd.DataFrame()

# --- MAIN PAGE LOGIC ---
st.title("🏢 Syndicate Property Forensics")
st.caption(f"Analyzing for Risk & Tax Arbitrage | Tax Rate: {MARGINAL_TAX_RATE*100}%")

df = load_property_data()

if not df.empty:
    # --- 1. DATA CLEANING & FORENSIC MATH ---
    # Convert columns to numbers
    num_cols = ['Original_Value', 'Current_Value', 'Initial_Annual_Income', 
                'Annual_Distribution', 'LVR_Percent', 'WALT_Years']
    
    for col in num_cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_val)

    # A. Capital Growth
    df['Capital_Gain_Pct'] = ((df['Current_Value'] - df['Original_Value']) / df['Original_Value'].replace(0, 1)) * 100
    
    # B. Yield Calculations (Cash Yield vs Original Yield)
    df['Current_Yield'] = (df['Annual_Distribution'] / df['Current_Value'].replace(0, 1))
    
    # C. Tax Arbitrage (The "Real" Yield)
    def calculate_tax_equiv(row):
        yield_val = row['Current_Yield']
        # Gross up PIE income to compare with Personal Rate (e.g. 17.5% or 39%)
        if str(row.get('Tax_Type', '')).upper() == 'PIE':
            gross_equiv = yield_val / (1 - MARGINAL_TAX_RATE)
            return gross_equiv
        else:
            return yield_val

    df['Tax_Equiv_Yield'] = df.apply(calculate_tax_equiv, axis=1)

    # D. Identify Debt Funds (Merx Logic)
    # If "Merx" or "Debt" is in the name, we treat WALT differently
    df['Is_Debt_Fund'] = df['Property_Name'].astype(str).str.contains("Merx|Debt", case=False, regex=True)

    # --- 2. FORENSIC ALERTS ---
    st.subheader("⚠️ Forensic Risk Report")
    
    col1, col2, col3 = st.columns(3)
    
    # ALERT: LVR STRESS
    high_lvr = df[df['LVR_Percent'] > LVR_WARNING]
    with col1:
        if not high_lvr.empty:
            st.error(f"**High Leverage (> {LVR_WARNING*100}%)**")
            for _, r in high_lvr.iterrows():
                st.write(f"🔴 **{r['Property_Name']}**: {r['LVR_Percent']*100:.1f}% LVR")
        else:
            st.success("✅ Leverage levels are safe.")

    # ALERT: WALT DECAY (Smart Filter for Merx)
    # Only flag low WALT if it is NOT a debt fund
    low_walt = df[
        (df['WALT_Years'] < WALT_WARNING) & 
        (df['WALT_Years'] > 0) & 
        (df['Is_Debt_Fund'] == False)
    ]
    
    with col2:
        if not low_walt.empty:
            st.warning(f"**Lease Expiry Risk (< {WALT_WARNING} yrs)**")
            for _, r in low_walt.iterrows():
                st.write(f"⚠️ **{r['Property_Name']}**: {r['WALT_Years']} years")
        else:
            st.success("✅ Lease terms are stable.")
            # Optional: Info note for Debt Funds
            debt_funds = df[df['Is_Debt_Fund'] == True]
            if not debt_funds.empty:
                st.caption(f"ℹ️ {len(debt_funds)} Debt Fund(s) excluded from WALT check.")

    # ALERT: INCOME COMPRESSION
    income_drop = df[df['Annual_Distribution'] < df['Initial_Annual_Income']]
    with col3:
        if not income_drop.empty:
            st.info("**Income Compression**")
            for _, r in income_drop.iterrows():
                drop = r['Initial_Annual_Income'] - r['Annual_Distribution']
                st.write(f"📉 **{r['Property_Name']}**: Down ${drop:,.0f}/yr")
        else:
            st.success("✅ Income is stable or growing.")

    # --- 3. VISUALIZATION ---
    st.markdown("---")
    c1, c2 = st.columns([2, 1])
    
    with c1:
        st.subheader("Yield Forensics: Cash vs. Tax-Adjusted")
        chart_df = df[['Property_Name', 'Current_Yield', 'Tax_Equiv_Yield']].copy()
        chart_df['Current_Yield'] = chart_df['Current_Yield'] * 100
        chart_df['Tax_Equiv_Yield'] = chart_df['Tax_Equiv_Yield'] * 100
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=chart_df['Property_Name'], y=chart_df['Current_Yield'],
            name='Cash Yield', marker_color='#636EFA'
        ))
        fig.add_trace(go.Bar(
            x=chart_df['Property_Name'], y=chart_df['Tax_Equiv_Yield'],
            name='Tax-Adjusted Yield', marker_color='#00CC96'
        ))
        fig.update_layout(barmode='group', height=400, margin=dict(t=0, b=0, l=0, r=0))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("The **Green Bar** is the equivalent pre-tax return required from a Bank/Stock to match this PIE yield.")

    with c2:
        st.subheader("Portfolio Weighting")
        fig_pie = px.pie(df, values='Current_Value', names='Owner_Entity', title="Value by Owner Entity", hole=0.4)
        fig_pie.update_layout(height=400, margin=dict(t=30, b=0, l=0, r=0))
        st.plotly_chart(fig_pie, use_container_width=True)

    # --- 4. DETAILED DATA TABLE ---
    st.subheader("📄 Forensic Data Tape")
    
    # Tag Debt Funds in the table for clarity
    df['Type_Tag'] = df['Is_Debt_Fund'].apply(lambda x: "DEBT FUND" if x else "PROPERTY")
    
    display_df = df[['Property_Name', 'Type_Tag', 'Current_Value', 'LVR_Percent', 'WALT_Years', 'Current_Yield', 'Tax_Equiv_Yield', 'Status']].copy()
    
    st.dataframe(
        display_df.style.format({
            "Current_Value": "${:,.0f}",
            "LVR_Percent": "{:.1%}",
            "WALT_Years": "{:.1f}",
            "Current_Yield": "{:.2%}",
            "Tax_Equiv_Yield": "{:.2%}"
        })
        .background_gradient(subset=['Tax_Equiv_Yield'], cmap="Greens")
        .background_gradient(subset=['LVR_Percent'], cmap="Reds", vmin=0.3, vmax=0.6),
        use_container_width=True, height=500
    )

else:
    st.info("👋 To start, please create a tab named 'Syndicate_Data' in your Google Sheet and add your property holdings.")
