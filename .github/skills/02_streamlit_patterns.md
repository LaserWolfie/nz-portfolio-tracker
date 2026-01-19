# Streamlit Patterns

## UI vs logic
- `pages/*` should mostly orchestrate:
  - read inputs
  - call `src/services/*`
  - render outputs
- Business logic lives in `src/services/*` and must be testable without Streamlit.

## Caching
- `st.cache_resource`: Sheets clients, API clients
- `st.cache_data(ttl=...)`: data pulls and computed frames

## Resilience
- Fail soft: show `st.warning/st.error`, dont crash app.
- Guard against missing session state.
- Avoid writing to Sheets unless user clicks an explicit button.

## No cell notes
- Do not rely on `cell.note` in gspread/worksheets.
- Store provenance in a text field (eg `Links`) or append-only log tabs.

