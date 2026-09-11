import streamlit as st
import pandas as pd
import plotly.express as px
import altair as alt
from datetime import datetime
import os
from modules import batch, deltas, extraction, narrative, schema, sheets, storage, utils

# --- CONFIGURATION ---
st.set_page_config(page_title="Property Forensics", page_icon="🏢", layout="wide")

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

# --- DATA LOADING ---
if 'prop_df' not in st.session_state or st.session_state.prop_df.empty:
    st.warning("⚠️ Data missing. Please go to the **Home** page first.")
    st.stop()

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
st.title("🏢 Property Forensics")

# Create Tabs to separate View vs Input
tab_dash, tab_upload, tab_batch, tab_review = st.tabs([
    "📊 Portfolio Dashboard",
    "📄 Upload Report (AI Scanner)",
    "📦 Batch Intake (Quarter)",
    "🔎 Quarterly Review",
])

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
    st.markdown("Upload an Annual Report PDF. Claude will extract the forensic data for you.")

    # A key is optional: without one the SDK falls back to ANTHROPIC_API_KEY in the
    # environment, an `ant auth login` profile, or Workload Identity Federation.
    if "ANTHROPIC_API_KEY" in st.secrets:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
        st.success("🔑 API key loaded from secrets")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        api_key = None
        st.success("🔑 Using credentials from the environment")
    else:
        api_key = st.text_input("Enter Anthropic API Key:", type="password") or None
        if not api_key:
            st.info("No key found. Enter one above, or set ANTHROPIC_API_KEY in the environment.")

    uploaded_file = st.file_uploader("Drag & Drop Report Here", type=['pdf'])
    expected_name = st.text_input(
        "Expected syndicate (optional)",
        help="Only used to flag a mismatch if the PDF turns out to be a different "
             "syndicate. It never overrides what the document says.",
    )

    have_credentials = bool(api_key) or bool(os.environ.get("ANTHROPIC_API_KEY"))
    if uploaded_file and have_credentials:
        if st.button("🚀 Scan Document", type="primary"):
            with st.spinner("🤖 Claude is reading the report..."):
                try:
                    report = extraction.extract_report(
                        pdf_bytes=uploaded_file.read(),
                        api_key=api_key,
                        syndicate_name=expected_name or None,
                    )
                except extraction.ExtractionError as e:
                    st.error(f"Extraction failed: {e}")
                else:
                    st.session_state['scanned_report'] = report
                    found, total = extraction.completeness(report)
                    if found == 0:
                        st.error(
                            "Nothing was extracted. The PDF may be image-only or not an "
                            "investor report."
                        )
                    elif found < total / 2:
                        st.warning(f"⚠️ Only {found} of {total} fields found — check the document.")
                    else:
                        st.success(f"✅ Extracted {found} of {total} fields.")

    # --- REVIEW THE EXTRACTION -------------------------------------------------
    report = st.session_state.get('scanned_report')
    if report is not None:
        st.markdown("---")
        st.subheader("🔍 Review before saving")
        st.caption(
            "Every figure is shown with the page it came from. Blank means the document "
            "did not state it — not zero."
        )

        rows = [
            {
                "Field": name.replace('_', ' ').title(),
                "Value": figure.value,
                "Page": figure.page,
                "Source": figure.source_text,
            }
            for name, figure in schema.iter_figures(report)
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if report.debt.swap_expiries:
            st.markdown("**Swap / hedge expiries**")
            st.dataframe(
                pd.DataFrame([s.model_dump() for s in report.debt.swap_expiries]),
                use_container_width=True, hide_index=True,
            )
        if report.conduct.manager_fees:
            st.markdown("**Manager fees by category**")
            st.dataframe(
                pd.DataFrame([f.model_dump() for f in report.conduct.manager_fees]),
                use_container_width=True, hide_index=True,
            )
        if report.extraction_notes:
            st.info(f"**Extraction notes:** {report.extraction_notes}")

        # --- SAVE TO Syndicate_Periods -------------------------------------
        st.markdown("##### Save")
        if not report.identity.period_end_date.value:
            st.error(
                "No period end date was found, so this report cannot be filed against a "
                "period. Check the document before saving."
            )
        else:
            try:
                spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
                baseline_ws = spreadsheet.worksheet(storage.BASELINE_WORKSHEET)
                baseline_rows = storage.load_baseline(baseline_ws)
            except Exception as e:
                st.error(f"Could not read {storage.BASELINE_WORKSHEET}: {e}")
                baseline_rows = []
                spreadsheet = None

            known_ids = [str(r.get('syndicate_id')) for r in baseline_rows if r.get('syndicate_id')]
            matched = storage.resolve_syndicate_id(report.identity.entity_name.value, baseline_rows)

            if matched:
                st.success(f"Matched to syndicate **{matched}**")
                syndicate_id = matched
            elif known_ids:
                st.warning(
                    f"Could not match “{report.identity.entity_name.value}” to a known syndicate. "
                    "Pick one, or add an alias in Syndicate_Baseline."
                )
                syndicate_id = st.selectbox("File under syndicate", known_ids)
            else:
                st.info(
                    f"{storage.BASELINE_WORKSHEET} is empty. Add a row there first so "
                    "reports can be filed against a stable syndicate id."
                )
                syndicate_id = None

            if syndicate_id and spreadsheet is not None:
                st.caption(
                    f"Saves to **{storage.PERIODS_WORKSHEET}** as "
                    f"`{syndicate_id}` / `{report.identity.period_end_date.value}`. "
                    "Re-saving the same period overwrites that row rather than adding one."
                )
                if st.button("💾 Save to sheet", type="primary"):
                    try:
                        status = storage.save_report(
                            report,
                            syndicate_id=syndicate_id,
                            source_filename=getattr(uploaded_file, 'name', ''),
                            model=extraction.DEFAULT_MODEL,
                            spreadsheet=spreadsheet,
                        )
                    except Exception as e:
                        st.error(f"Save failed: {e}")
                    else:
                        st.success(f"Saved ({status}).")

# ==========================================
# TAB 3: BATCH INTAKE
# ==========================================
with tab_batch:
    st.header("📦 Batch Intake")
    st.markdown(
        "Upload a whole quarter's reports at once. Matching runs first and costs "
        "nothing, so you can check the plan before spending anything on extraction."
    )

    if not have_credentials:
        st.info("Set ANTHROPIC_API_KEY in secrets or the environment to run a batch.")
    else:
        uploads = st.file_uploader(
            "Drop this quarter's PDFs here",
            type=['pdf'],
            accept_multiple_files=True,
            key="batch_uploads",
        )

        if uploads:
            try:
                batch_spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
                batch_baseline = storage.load_baseline(
                    batch_spreadsheet.worksheet(storage.BASELINE_WORKSHEET)
                )
            except Exception as e:
                st.error(f"Could not read {storage.BASELINE_WORKSHEET}: {e}")
                batch_baseline, batch_spreadsheet = [], None

            # --- Plan: free, no API calls ---
            plan = batch.plan_batch([f.name for f in uploads], batch_baseline)
            unmatched = [i for i in plan if i.status == batch.Status.UNMATCHED]

            st.subheader("1. Match plan")
            st.dataframe(
                pd.DataFrame([{
                    "File": i.filename,
                    "Matched syndicate": i.syndicate_id or "— will use the document —",
                } for i in plan]),
                use_container_width=True, hide_index=True,
            )
            if unmatched:
                st.warning(
                    f"{len(unmatched)} file(s) did not match on filename. They will still "
                    "be extracted — the entity name inside the document is authoritative "
                    "and usually resolves them."
                )
            st.caption(
                f"{len(plan)} document(s). Estimated extraction cost "
                f"**~${batch.estimated_cost(plan):.2f}** at {extraction.DEFAULT_MODEL} rates. "
                "Matching above was free."
            )

            st.subheader("2. Extract")
            if st.button(f"🚀 Extract {len(plan)} document(s)", type="primary"):
                progress = st.progress(0.0, text="Starting…")
                documents = {f.name: f.read() for f in uploads}

                def _tick(done, total, item):
                    progress.progress(done / total, text=f"{done}/{total} — {item.filename}")

                results = batch.run_batch(
                    documents, batch_baseline,
                    api_key=api_key, model=extraction.DEFAULT_MODEL, on_progress=_tick,
                )
                progress.empty()
                st.session_state['batch_results'] = results

        results = st.session_state.get('batch_results')
        if results:
            st.subheader("3. Results")
            counts = batch.summarise(results)
            st.write(" · ".join(f"**{n}** {status}" for status, n in sorted(counts.items())))

            st.dataframe(
                pd.DataFrame([{
                    "File": i.filename,
                    "Status": i.status,
                    "Syndicate": i.syndicate_id or "",
                    "Period": i.period_end or "",
                    "Found": f"{i.completeness[0]}/{i.completeness[1]}" if i.completeness else "",
                    "Note": (i.error or " ".join(i.notes))[:140],
                } for i in results]),
                use_container_width=True, hide_index=True,
            )

            attention = [i for i in results if i.needs_attention]
            if attention:
                st.warning(f"{len(attention)} document(s) need a decision before saving.")
                for i in attention:
                    with st.expander(f"{i.status.upper()} — {i.filename}"):
                        if i.error:
                            st.error(i.error)
                        for note in i.notes:
                            st.write(f"• {note}")

            ready = [i for i in results
                     if i.report and i.syndicate_id and i.period_end
                     and i.status == batch.Status.EXTRACTED]
            conflicts = [i for i in results if i.status == batch.Status.CONFLICT]
            sparse = [i for i in results if i.status == batch.Status.SPARSE]

            st.subheader("4. Save")
            include = False
            include_sparse = False
            if conflicts:
                include = st.checkbox(
                    f"Also save {len(conflicts)} conflicted document(s), filing each under "
                    "the syndicate named inside the document",
                    value=False,
                )
            if sparse:
                include_sparse = st.checkbox(
                    f"Also save {len(sparse)} sparse document(s) — usually tax statements "
                    "or valuation letters rather than full reports. Saving one makes the "
                    "fields it omits look withdrawn next quarter.",
                    value=False,
                )
            total_to_save = (len(ready) + (len(conflicts) if include else 0)
                             + (len(sparse) if include_sparse else 0))
            st.caption(
                f"{total_to_save} row(s) will be written to {storage.PERIODS_WORKSHEET}. "
                "Re-saving a period overwrites that row rather than adding one."
            )
            if total_to_save and st.button(f"💾 Save {total_to_save} row(s)"):
                batch.save_batch(
                    results, spreadsheet=batch_spreadsheet,
                    model=extraction.DEFAULT_MODEL, include_conflicts=include,
                    include_sparse=include_sparse,
                )
                saved = sum(1 for i in results if i.status == batch.Status.SAVED)
                st.success(f"Saved {saved} row(s).")
                st.session_state['batch_results'] = results


# ==========================================
# TAB 4: FLAGS AND NOTES
# ==========================================
with tab_review:
    st.header("🔎 Quarterly Review")
    st.markdown(
        "Ranked by what needs attention. Flags come from stored figures only — no "
        "document is re-read, so every statement traces back to a saved number."
    )

    try:
        review_ss = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
        period_rows = review_ss.worksheet(storage.PERIODS_WORKSHEET).get_all_records()
        baseline_rows = storage.load_baseline(
            review_ss.worksheet(storage.BASELINE_WORKSHEET))
    except Exception as e:
        st.error(f"Could not read the storage tabs: {e}")
        period_rows, baseline_rows, review_ss = [], [], None

    if not period_rows:
        st.info(
            f"{storage.PERIODS_WORKSHEET} is empty. Extract and save some reports first."
        )
    else:
        baseline_by_id = {str(b.get('syndicate_id')): b for b in baseline_rows}
        reviews = deltas.review_all(period_rows, baseline_rows)
        attention = [r for r in reviews if r.needs_attention]

        c1, c2, c3 = st.columns(3)
        c1.metric("Syndicates stored", len(reviews))
        c2.metric("Needing attention", len(attention))
        c3.metric("Flags raised", sum(len(r.flags) for r in reviews))

        severity_icon = {
            "CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪",
        }

        for review in reviews:
            name = baseline_by_id.get(review.syndicate_id, {}).get(
                'canonical_name', review.syndicate_id)
            icon = severity_icon.get(str(review.worst), "⚪")
            label = f"{icon} {name} — {review.period_end} ({len(review.flags)} flag(s))"

            with st.expander(label, expanded=review.needs_attention):
                for f in review.flags:
                    line = f"{severity_icon.get(str(f.severity), '')} **{f.severity}** — {f.message}"
                    if f.severity >= deltas.Severity.HIGH:
                        st.error(line)
                    elif f.severity == deltas.Severity.MEDIUM:
                        st.warning(line)
                    else:
                        st.info(line)
                if not review.flags:
                    st.success("No flags this period.")

                row = next((r for r in period_rows
                            if str(r.get('syndicate_id')) == review.syndicate_id
                            and str(r.get('period_end')) == review.period_end), None)

                key = f"note_{review.syndicate_id}_{review.period_end}"
                if st.button("✍️ Draft note and manager questions", key=f"btn_{key}"):
                    if not have_credentials:
                        st.error("Set ANTHROPIC_API_KEY to draft notes.")
                    else:
                        with st.spinner("Writing from the stored figures…"):
                            try:
                                st.session_state[key] = narrative.write_narrative(
                                    row,
                                    review.flags,
                                    deltas.prior_period(period_rows, review.syndicate_id,
                                                        review.period_end),
                                    baseline_by_id.get(review.syndicate_id),
                                    api_key=api_key,
                                )
                            except Exception as e:
                                st.error(f"Could not draft the note: {e}")

                note = st.session_state.get(key)
                if note:
                    st.markdown(f"**{note.headline}**")
                    st.write(note.note)
                    if note.questions:
                        st.markdown("**Questions for the manager**")
                        for i, q in enumerate(note.questions, 1):
                            st.write(f"{i}. {q.question}")
                            st.caption(f"basis: {q.basis}")
                    if note.data_gaps:
                        st.caption("Not disclosed: " + "; ".join(note.data_gaps))

                    facts = narrative.build_facts(
                        row, review.flags,
                        deltas.prior_period(period_rows, review.syndicate_id,
                                            review.period_end),
                        baseline_by_id.get(review.syndicate_id))
                    stray = narrative.unsupported_numbers(note, facts)
                    if stray:
                        st.error(
                            "These numbers do not appear in the stored figures and may be "
                            f"invented: {', '.join(stray)}"
                        )
                    else:
                        st.caption("✅ Every figure quoted traces back to a stored value.")
