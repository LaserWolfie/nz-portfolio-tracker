# Return Ladder (Template Viewer)  Rules

## Source of truth
Google Sheet tabs (typical):
- `APP_INPUTS`: main input table (Ticker/Market + key fields used by model)
- `APP_SOURCES`: auto-seeded URLs per ticker/field
- `Sources` (wide): curated values like `NetCash_bn`, `FCF1_bn`, and URLs
- `SOURCES_LOG`: append-only extraction log (timestamp/ticker/field/value/source)

## Write safety
- Autofill actions must:
  - fill blanks only
  - never overwrite non-empty user-entered values
  - append provenance into `Links` (or log tabs)

## Implementation expectations
- Keep schema + seeding in `src/services/return_ladder_app_inputs.py`
- Any new autofill should have:
  - a small helper in `src/services/`
  - a smoketest script in `scripts/`
  - diagnostics output that prints what was filled and why

## Acceptance checklist (when changing return ladder)
- Add ticker from app  row exists in `APP_INPUTS`
- Company/Price/Shares populate where supported
- Clicking autofill button fills the intended fields
- No cell notes used
- Provenance stored safely (`Links` or log)

