from src.data.sheets import ensure_data_loaded, get_gspread_client, load_property_df
from src.config import PROPERTY_SHEET_ID, PROPERTY_TAB
ensure_data_loaded()

import streamlit as st
import pandas as pd
import plotly.express as px
import altair as alt
from datetime import datetime
import os
import re
import google.generativeai as genai
import gspread
from gspread.utils import rowcol_to_a1
import json
from modules import utils

ENABLE_AI_UPSERT = False

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
def is_blankish(x):
    if x is None:
        return True
    s = str(x).strip().lower()
    return s in {"", "-", "none", "null", "nan", "n/a", "#n/a", "#value!", "#div/0!"}


def clean_number(value, default=0.0, nan_on_invalid=False):
    try:
        return utils.clean_number(value, default=default, nan_on_invalid=nan_on_invalid)
    except TypeError:
        result = utils.clean_number(value, default=default)
        if nan_on_invalid and result == default and is_blankish(value):
            return float("nan")
        return result

def clean_percent(x):
    return utils.clean_percent(x)

def normalize_whitelist_value(field, value):
    if value is None:
        return None
    percent_fields = {"lvr_percent", "vacancy_percent", "expense_ratio", "debt_yield"}
    s = str(value).strip()
    if s == "":
        return None
    s_lower = s.lower()
    if s_lower == "nil":
        return 0.0 if field == "vacancy_percent" else None
    if s_lower in {"-", "none", "null", "nan", "n/a", "na"}:
        return 0.0 if field == "vacancy_percent" else None
    if field == "interest_cover":
        s = s.replace("x", "").replace("X", "").strip()
    if field == "loan_expiry_year":
        match = re.search(r"\b(19|20)\d{2}\b", s)
        if match:
            year = int(match.group(0))
            return year if 1900 <= year <= 2100 else None
        try:
            year = int(float(s.replace(",", "")))
            return year if 1900 <= year <= 2100 else None
        except Exception:
            return None
    has_percent = "%" in s
    if any(ch in s.upper() for ch in ["M", "B", "K"]) and not has_percent:
        return None
    s_num = s.replace("$", "").replace(",", "").replace("%", "").replace("NZD", "").strip()
    try:
        num = float(s_num)
    except Exception:
        return None
    if field in percent_fields:
        if has_percent or num > 1.0:
            if num > 100.0:
                return None
            num = num / 100.0
        return num
    return num


def update_sheet_whitelist_fields(data_dict):
    entity_name = str(data_dict.get("Entity_Name", "")).strip()
    if not entity_name:
        st.error("Entity_Name is required to update.")
        return False

    whitelist = {
        "LVR_Percent",
        "WALT_Years",
        "Vacancy_Percent",
        "Expense_Ratio",
        "Debt_Yield",
        "Loan_Expiry_Year",
        "Interest_Cover",
    }

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(PROPERTY_SHEET_ID).worksheet(PROPERTY_TAB)
        values = sheet.get_all_values()
        if not values:
            st.error("Property sheet has no headers.")
            return False

        headers = values[0]
        header_norm = [str(h).strip().lower().replace(" ", "_") for h in headers]
        if "entity_name" not in header_norm:
            st.error("Entity_Name column not found.")
            return False

        entity_col = header_norm.index("entity_name")
        target_row = None
        for idx, row_vals in enumerate(values[1:], start=2):
            existing = str(row_vals[entity_col]).strip()
            if existing.lower() == entity_name.lower():
                target_row = idx
                break

        if not target_row:
            st.error("Entity_Name not found in sheet. No changes made.")
            return False

        updated = 0
        for key, value in data_dict.items():
            if key not in whitelist:
                continue
            norm_key = key.strip().lower().replace(" ", "_")
            if norm_key not in header_norm:
                continue
            norm_val = normalize_whitelist_value(norm_key, value)
            if norm_val is None:
                continue
            col_idx = header_norm.index(norm_key)
            sheet.update_cell(target_row, col_idx + 1, norm_val)
            updated += 1

        if updated:
            st.cache_data.clear()
            load_property_df.clear()
            st.session_state.prop_df = load_property_df()
            st.success("Updated Google Sheet (whitelist fields only).")
        else:
            st.info("No changes needed.")
        return True
    except Exception as e:
        st.error(f"Update Error: {e}")
        return False

def post_process_distributions(data_dict):
    cleared = False
    for key in ("Annual_Distribution", "Original_Distribution"):
        val = data_dict.get(key)
        if isinstance(val, str) and "%" in val:
            data_dict[key] = None
            cleared = True
            continue
        parsed = clean_number(val, nan_on_invalid=True)
        if pd.isna(parsed):
            data_dict[key] = None
            continue
        if parsed < 500:
            data_dict[key] = None
            cleared = True
            continue
        data_dict[key] = parsed
    if cleared:
        st.warning(
            "Cash distribution not confidently found; field cleared to prevent overwriting. "
            "Enter cash $ manually if known."
        )
    return data_dict

def save_to_google_sheet(data_dict):
    """Upserts extracted data to the Google Sheet (match by Entity_Name)."""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(PROPERTY_SHEET_ID).worksheet(PROPERTY_TAB)

        values = sheet.get_all_values()
        if not values:
            st.error("Save Error: Property sheet has no headers.")
            return False

        headers = values[0]
        header_norm = [str(h).strip().lower().replace(" ", "_") for h in headers]
        if "entity_name" not in header_norm:
            st.error("Save Error: Entity_Name column not found.")
            return False

        data_norm = {str(k).strip().lower().replace(" ", "_"): v for k, v in data_dict.items()}
        header_map = {name: idx for idx, name in enumerate(header_norm)}

        entity_value = str(data_norm.get("entity_name", "")).strip()
        if not entity_value:
            st.error("Save Error: Entity_Name is required for upsert.")
            return False

        target_row = None
        entity_col_idx = header_norm.index("entity_name")
        for idx, row_vals in enumerate(values[1:], start=2):
            existing = str(row_vals[entity_col_idx]).strip()
            if existing.lower() == entity_value.lower():
                target_row = idx
                break

        whitelist = [
            "lvr_percent",
            "walt_years",
            "vacancy_percent",
            "expense_ratio",
            "debt_yield",
            "loan_expiry_year",
            "interest_cover",
        ]

        if target_row:
            for field in whitelist:
                if field not in header_map:
                    continue
                if field not in data_norm:
                    continue
                norm_val = normalize_whitelist_value(field, data_norm[field])
                if norm_val is None:
                    continue
                sheet.update_cell(target_row, header_map[field] + 1, norm_val)
        else:
            row = ["" for _ in headers]
            row[entity_col_idx] = entity_value
            for field in whitelist:
                if field not in header_map or field not in data_norm:
                    continue
                norm_val = normalize_whitelist_value(field, data_norm[field])
                if norm_val is None:
                    continue
                row[header_map[field]] = norm_val
            sheet.append_row(row)
        load_property_df.clear()
        st.session_state.prop_df = load_property_df()
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
for col in ['LVR_Percent', 'Vacancy_Percent', 'Expense_Ratio', 'Debt_Yield']:
    if col in df.columns:
        df[col] = df[col].apply(clean_percent)

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
if "pf_view" not in st.session_state:
    st.session_state["pf_view"] = "Upload Report (AI Scanner)"
view = st.radio(
    "View",
    ["Upload Report (AI Scanner)", "Portfolio Dashboard"],
    horizontal=True,
    key="pf_view",
    label_visibility="collapsed",
)
st.title("🏢 NZ Wealth Manager Pro — Property Forensics")

# ==========================================
# TAB 1: FORENSIC DASHBOARD
# ==========================================
if view == "Portfolio Dashboard":
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
        st.write(
            df[['Entity_Name', 'Interest_Cover', 'Vacancy_Percent', 'Expense_Ratio']].rename(
                columns={'Interest_Cover': 'Interest_Cover (Net Profit / Interest)'}
            )
        )

    st.markdown("---")

    # --- 3. DETAILED TABLE ---
    st.subheader(f"🔎 Syndicate Details ({scenario_label})")
    display_cols = ['Entity_Name', 'Owner_Entity', 'Original_Value', 'Annual_Distribution', 'Scenario_Distribution', 'Scenario_Yield', 'LVR_Percent']
    st.dataframe(df[display_cols].style.format({"Original_Value": "${:,.0f}", "Annual_Distribution": "${:,.0f}", "Scenario_Distribution": "${:,.0f}"}), use_container_width=True)
# ==========================================
# TAB 2: AI REPORT SCANNER
# ==========================================
else:
    st.header("📄 PDF Report Scanner")
    with st.expander("How to calculate LVR (Loan-to-Value)"):
        st.write("LVR = Interest-bearing debt  Property value")
        st.markdown(
            "- Interest-bearing debt (Borrowings / Bank loan / Facility / Term liabilities)  "
            "sum current + non-current if split; exclude trade payables\n"
            "- Property value (Investment property / Property assets / Valuation / Fair value)"
        )
        st.markdown("Debt = $A\nProperty value = $B\nLVR = A  B = X%")
    st.caption("Tip: Use interest-bearing debt only (not all liabilities).")
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
                    
                    prompt = (
                        "Return ONLY valid JSON (no markdown). If unknown, use null. "
                        "Do NOT output or guess: Owner_Entity, Manager, Sector, "
                        "Entity_Name casing changes, Original_Value, Current_Value, "
                        "Original_Annual_Distribution, Annual_Distribution. "
                        "Return ONLY these keys with types: "
                        "{"
                        "\"Entity_Name\": string, "
                        "\"LVR_Percent\": number | null (decimal fraction, e.g. 0.3069), "
                        "\"WALT_Years\": number | null, "
                        "\"Vacancy_Percent\": number | null (decimal fraction; interpret \"Nil\" as 0), "
                        "\"Expense_Ratio\": number | null (decimal fraction), "
                        "\"Debt_Yield\": number | null (decimal fraction), "
                        "\"Loan_Expiry_Year\": integer | null, "
                        "\"Interest_Cover\": number | null (numeric ratio, no \"x\")"
                        "}. "
                        "Prefer computing Interest_Cover from the income statement "
                        "(operating profit before finance / interest expense). "
                        "If you cannot compute confidently, return null."
                    )
                    response = model.generate_content([{'mime_type': 'application/pdf', 'data': pdf_data}, prompt])
                    cleaned_text = response.text.replace('```json', '').replace('```', '').strip()
                    scanned_data = json.loads(cleaned_text)
                    if isinstance(scanned_data, dict):
                        scanned_data = post_process_distributions(scanned_data)
                    if isinstance(scanned_data, dict):
                        entity_name = str(scanned_data.get("Entity_Name", "")).strip()
                        if not entity_name:
                            fallback = os.path.splitext(uploaded_file.name)[0]
                            fallback = fallback.replace("_", " ").replace("-", " ").strip()
                            scanned_data["Entity_Name"] = fallback
                            st.warning("Entity_Name missing; used filename fallback.")
                    st.session_state["scanned_data"] = scanned_data
                    st.success("✅ Extraction Complete!")
                except Exception as e:
                    st.error(f"AI Error: {e}")

    if "scanned_data" in st.session_state:
        st.subheader("Review Extracted Data")
        st.caption("Interest_Cover = Net Profit / Interest Expense (not EBIT).")
        with st.expander("How Interest_Cover is calculated"):
            st.write("Formula: Net Profit / Interest Expense")
            st.write("EBIT-style cover is different; this app uses the Net Profit basis.")
        with st.expander("Review extracted data (before updating Google Sheet)"):
            st.json(st.session_state["scanned_data"])
            edit_df = pd.DataFrame([st.session_state["scanned_data"]])
            edited_df = st.data_editor(edit_df, num_rows="fixed", use_container_width=True)
            entity_name = str(edited_df.iloc[0].get("Entity_Name", "")).strip()
            if entity_name:
                try:
                    client = get_gspread_client()
                    sheet = client.open_by_key(PROPERTY_SHEET_ID).worksheet(PROPERTY_TAB)
                    values = sheet.get_all_values()
                    if values:
                        headers = values[0]
                        header_norm = [str(h).strip().lower().replace(" ", "_") for h in headers]
                        if "entity_name" in header_norm:
                            entity_col = header_norm.index("entity_name")
                            target_row = None
                            for idx, row_vals in enumerate(values[1:], start=2):
                                existing = str(row_vals[entity_col]).strip()
                                if existing.lower() == entity_name.lower():
                                    target_row = idx
                                    break
                            if target_row:
                                row_vals = values[target_row - 1]
                                preview_rows = []
                                fields = [
                                    "LVR_Percent",
                                    "WALT_Years",
                                    "Vacancy_Percent",
                                    "Expense_Ratio",
                                    "Debt_Yield",
                                    "Loan_Expiry_Year",
                                    "Interest_Cover",
                                ]
                                for field in fields:
                                    norm_key = field.lower()
                                    if norm_key not in header_norm:
                                        continue
                                    col_idx = header_norm.index(norm_key)
                                    current_raw = row_vals[col_idx] if col_idx < len(row_vals) else ""
                                    new_raw = edited_df.iloc[0].get(field)
                                    current_norm = normalize_whitelist_value(norm_key, current_raw)
                                    new_norm = normalize_whitelist_value(norm_key, new_raw)
                                    will_update = new_norm is not None and (
                                        current_norm is None or abs(float(new_norm) - float(current_norm)) > 1e-9
                                    )
                                    preview_rows.append(
                                        {
                                            "Field": field,
                                            "Current": current_norm if current_norm is not None else current_raw,
                                            "New": new_norm if new_norm is not None else new_raw,
                                            "Will Update?": bool(will_update),
                                        }
                                    )
                                if preview_rows:
                                    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)
                            else:
                                st.info("Entity_Name not found in sheet. Preview unavailable.")
                except Exception:
                    st.info("Preview unavailable (sheet lookup failed).")
            reviewed_ok = st.checkbox("I have reviewed the data and it looks OK", value=False)
            if st.button("Update Google Sheet", disabled=not reviewed_ok):
                update_sheet_whitelist_fields(edited_df.iloc[0].to_dict())
