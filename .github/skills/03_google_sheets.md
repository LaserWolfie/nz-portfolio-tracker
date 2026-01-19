# Google Sheets Integration (gspread)

## Secrets
- Credentials must come from Streamlit secrets (service account JSON dict).
- Local dev can use `.streamlit/secrets.toml` (gitignored).

## Performance & safety
- Batch reads/writes (avoid per-cell operations).
- Scope writes tightly to specific columns/rows.
- Never clear whole sheets unless explicitly designed & documented.

## Provenance
- Do NOT use cell notes.
- Prefer:
  - `Links` column for URLs
  - `*_LOG` tab for append-only provenance rows:
    - timestamp, ticker, field, value, source_url

## Matching rules
- Prefer exact ticker matching when possible.
- For aliases (EBO vs EBOS), maintain a small alias map in code (or a Sheet tab).
- For company-name matching: normalize (upper, strip punctuation, remove LTD/LIMITED).

