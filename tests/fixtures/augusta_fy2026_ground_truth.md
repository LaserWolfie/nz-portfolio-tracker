# Ground truth — Augusta St Georges Bay Road Property Trust, FY2026

Source: `Augusta St Georges Bay Road Property Trust - Annual Report - June 2026.pdf`
(44 pages). Established by reading the report directly, **not** by model extraction, so
it can be used to score extraction runs.

Cross-checks used where the document is ambiguous are noted.

| Schema field | Expected value | Where / note |
|---|---|---|
| `entity_name` | Augusta St Georges Bay Road Property Trust | p1 cover |
| `manager_name` | Centuria Funds Management (NZ) Limited | Scheme information |
| `period_end_date` | 2026-03-31 | "year ending 31 March 2026" |
| `report_type` | Annual Report | p1 cover |
| `valuation` | 115000000 | Summary tile; "$115.00 million" in letter; 115,000,000 in scheme property table |
| `valuation_date` | 2026-03-31 | "The 31 March 2026 valuation" |
| `nta_per_unit` | 45431 | Financial statements "Net assets per unit 45,431". Summary tile rounds to "$45K" |
| `cash` | 1953096 | "cash balance increased by $814,884 to $1,953,096" |
| `total_debt` | 53500000 | Summary tile "FY2026 Loan balance $53.5M" |
| `facility_expiry` | 2026-09-30 | **Narrative only** — "reflecting its maturity on 30 September 2026" |
| `lvr_percent` | 46.52 | Summary tile. Letter rounds to "an LVR of 47%" |
| `icr_actual` | **null** | Not disclosed anywhere |
| `icr_covenant` | **null** | Not disclosed. "Covenant Trustee Services Limited" is the *supervisor* — a trap |
| `swap_expiries` | `[]` or near-empty | Swaps exist (fair value $47,343) but no expiry or notional is disclosed |
| `occupancy_percent` | **null** | Not disclosed |
| `wale_years` | 3.35 | Summary tile. Letter rounds to "3.4 years" |
| `distribution_rate` | 6.75 | "The average distribution rate remained at 6.75%" |
| `distribution_unit` | `percent_per_annum_on_subscription_price` | Defined as % of original investment |
| `payout_ratio_percent` | 87 | "The payout ratio was 87% (2025: 96%)" |
| `adjusted_operating_profit` | 5303580 | "Adjusted net profit 5,303,580" |
| `adjusted_operating_profit_forecast` | **null** | Annual reports carry no forecast column |
| `manager_fees` | Scheme management 0.42%, Property management 0.07% | **Percentages of scheme property, not dollars** |

## Traps this document contains

1. **Scrambled stat tiles.** The key information summary is a visual grid. Linear text
   extraction separates every label from its value, yielding a pile of orphaned numbers
   (`7.43%`, `6.75%`, `$115.0M`, `$53.5M`, `$50K`, `3.35 years`, `$45K`, `46.52%`) and a
   pile of orphaned captions. This is the concrete reason the PDF must go to the model as
   a `document` block rather than pre-extracted text.

2. **Two distribution rates, both correct.** Average distribution rate 6.75% (on original
   investment of $50,000) and distribution yield 7.43% (on closing equity of $45,431).
   They reconcile: 6.75% x 50,000 = 7.43% x 45,431 = ~$3,375 per unit. Picking the wrong
   one silently corrupts period-on-period comparison.

3. **The manager's letter contradicts the summary.** The letter calls 7.43% "the average
   distributions paid to investors", while the summary labels 7.43% as distribution yield
   and 6.75% as the average distribution rate. The document is internally inconsistent in
   its naming.

4. **Rounded narrative vs precise tiles.** LVR 47% vs 46.52%; WALE 3.4 vs 3.35 years.

5. **"Covenant" as a company name.** `Covenant Trustee Services Limited` is the supervisor
   and has nothing to do with a banking covenant.

6. **Facility expiry hidden in prose**, inside a paragraph about reclassifying the loan
   from non-current to current liabilities.

## What this implies for the delta engine

Facility expiry of 2026-09-30 against a 2026-03-31 balance date is **six months out** —
comfortably inside the 18-month window, and the highest-severity flag this report should
produce. The loan has already been reclassified to current liabilities, which is the
accounting system saying the same thing.
