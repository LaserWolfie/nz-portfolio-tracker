"""Quarterly Review — the page for when a quarter's reports all arrive at once.

Structure everything, then read only what is flagged. Batch intake on the first
tab, the ranked worklist on the second.

Property Forensics keeps the portfolio dashboard and the ad-hoc single-document
scanner; this page owns the quarterly cycle.
"""

import streamlit as st
import pandas as pd

from modules import batch, deltas, extraction, narrative, sheets, storage, ui

st.set_page_config(page_title="Quarterly Review", page_icon="🔎", layout="wide")

# Pages cannot be opened directly; Home must run first to fill session_state.
ui.require_portfolio_data()

st.title("🔎 Quarterly Review")

api_key, have_credentials = ui.anthropic_credentials()
spreadsheet, period_rows, baseline_rows = ui.load_pipeline_data()

tab_intake, tab_flags = st.tabs([
    "📦 Batch Intake",
    "🚩 Flags & Notes",
])

# ==========================================
# BATCH INTAKE
# ==========================================
with tab_intake:
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
            batch_spreadsheet, batch_baseline = spreadsheet, baseline_rows

            # --- Plan: free, no API calls ---
            plan = batch.plan_batch([f.name for f in uploads], batch_baseline)
            unmatched = [i for i in plan if i.status == batch.Status.UNMATCHED]

            st.subheader("1. Match plan")
            st.dataframe(
                pd.DataFrame([{
                    "File": i.filename,
                    "Kind": i.kind,
                    "Matched syndicate": i.syndicate_id or "— will use the document —",
                } for i in plan]),
                use_container_width=True, hide_index=True,
            )

            admin = [i for i in plan if i.kind == "administrative"]
            skip_admin = True
            if admin:
                skip_admin = st.checkbox(
                    f"Skip {len(admin)} administrative document(s) — proxy forms, meeting "
                    "notices, disclosure statements. They carry no periodic figures and "
                    "cost the same to extract as a full report.",
                    value=True,
                )
            plan = batch.worth_extracting(plan) if skip_admin else plan
            if unmatched:
                st.warning(
                    f"{len(unmatched)} file(s) did not match on filename. They will still "
                    "be extracted — the entity name inside the document is authoritative "
                    "and usually resolves them."
                )
            st.caption(
                f"**{len(plan)} document(s) will be extracted.** Estimated cost "
                f"**~${batch.estimated_cost(plan):.2f}** at {extraction.DEFAULT_MODEL} rates. "
                "Matching above was free."
            )

            st.subheader("2. Extract")
            wanted = {i.filename for i in plan}
            if plan and st.button(f"🚀 Extract {len(plan)} document(s)", type="primary"):
                progress = st.progress(0.0, text="Starting…")
                documents = {f.name: f.read() for f in uploads if f.name in wanted}

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
# FLAGS AND NOTES
# ==========================================
with tab_flags:
    st.header("🚩 Flags & Notes")
    st.markdown(
        "Ranked by what needs attention. Flags come from stored figures only — no "
        "document is re-read, so every statement traces back to a saved number."
    )

    if st.button("🔄 Reload stored rows", key="reload_periods"):
        ui._property_spreadsheet.clear()
        st.rerun()

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

        for review in reviews:
            name = baseline_by_id.get(review.syndicate_id, {}).get(
                'canonical_name', review.syndicate_id)
            icon = ui.SEVERITY_ICON.get(str(review.worst), "⚪")
            label = f"{icon} {name} — {review.period_end} ({len(review.flags)} flag(s))"

            with st.expander(label, expanded=review.needs_attention):
                for f in review.flags:
                    ui.render_flag(f)
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
