import pandas as pd

def calculate_nz_tax_exposure(stock_df):
    """
    Identifies if the portfolio exceeds the NZD $50,000 cost basis for FIF rules.
    Expects a DataFrame with 'Ticker' and 'Cost_Basis' columns.
    """
    if stock_df.empty or 'Ticker' not in stock_df.columns:
        return {
            "fif_active": False,
            "foreign_cost_basis": 0,
            "recommendation": "No data available"
        }

    # 1. Standardize Tickers to uppercase for comparison
    # 2. Filter out NZ and ASX exempt-style tickers (ending in .NZ or .AX)
    # Note: This is a heuristic; specific ASX stocks may still be FIF-exempt.
    foreign_stocks = stock_df[
        ~stock_df['Ticker'].str.upper().str.endswith(('.NZ', '.AX'), na=False)
    ].copy()
    
    # Ensure Cost_Basis is numeric (fallback if not already cleaned)
    if 'Cost_Basis' in foreign_stocks.columns:
        total_cost_basis = pd.to_numeric(foreign_stocks['Cost_Basis'], errors='coerce').sum()
    else:
        total_cost_basis = 0
        
    fif_active = total_cost_basis > 50000
    
    return {
        "fif_active": fif_active,
        "foreign_cost_basis": total_cost_basis,
        "recommendation": "Use FDR (5%) or CV Method" if fif_active else "De Minimis: Standard RWT applies"
    }