# Testing & Safeties

## Minimum bar (every PR)
- `python -m compileall -q .` (catch SyntaxError quickly)
- Run the relevant smoketest scripts in `scripts/`
- `streamlit run app.py` and click through key pages

## Smoketest conventions
- Put scripts in `scripts/`
- Print:
  - what tickers were tested
  - what fields were found/missing
  - what URLs were used
- Exit non-zero on failure when appropriate.

## Never
- Dont commit secrets.
- Dont write broad clear sheet operations.
- Dont implement fragile scraping without timeouts + error handling.

