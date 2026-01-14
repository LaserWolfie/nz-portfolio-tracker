import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yfinance as yf
import time
import pandas as pd

# --- CONFIG ---
SHEET_NAME = "Share Portfolio"
SECTOR_COL_NAME = "Sector" # Ensure this header exists in your sheet first!

print("🔌 Connecting to Google Sheets...")
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).worksheet("Share Portfolio")

# 1. Get all data
data = sheet.get_all_values()
headers = data[0]
df = pd.DataFrame(data[1:], columns=headers)

# Find the index of the 'Sector' column (0-based index)
try:
    sector_col_index = headers.index(SECTOR_COL_NAME) + 1 # +1 because gspread is 1-based
except ValueError:
    print(f"❌ Error: Could not find a column named '{SECTOR_COL_NAME}'. Please add it to row 1 of your sheet.")
    exit()

# 2. Loop through tickers
print(f"🔎 Scanning {len(df)} stocks for Sector data (with 1s delay to avoid blocks)...")

updates = []

for i, row in df.iterrows():
    ticker = row['Ticker']
    
    # Skip empty rows
    if not ticker: continue
    
    # Fix Ticker format
    yahoo_ticker = ticker.strip().upper()
    if ":" in yahoo_ticker:
        code = yahoo_ticker.split(":")[-1]
        yahoo_ticker = code + ".AX" if "ASX" in yahoo_ticker else code + ".NZ"

    print(f"   Fetching: {yahoo_ticker}...", end=" ")
    
    try:
        # Fetch info
        info = yf.Ticker(yahoo_ticker).info
        sector = info.get('sector', 'Unknown')
        print(f"-> {sector}")
        
        # Prepare update: (Row Number, Column Number, Value)
        # Row is i + 2 (1 for header, 1 for 0-index)
        if sector != 'Unknown':
            sheet.update_cell(i + 2, sector_col_index, sector)
        
    except Exception as e:
        print(f"Error: {e}")
    
    # CRITICAL: Sleep to prevent Yahoo blocking us
    time.sleep(1.0) 

print("✅ Done! Sectors saved to Google Sheet.")