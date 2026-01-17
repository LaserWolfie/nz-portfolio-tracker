from src.services import market_data
from src.services.market_data import Quote
from src.services.fundamentals import compute_net_cash_from_companyfacts, get_cik_for_ticker
from pathlib import Path
import json


def test_fetch_us_quote_uses_yfinance_for_shares(monkeypatch):
    def fake_google(ticker, exchange=None):
        return Quote(
            ticker=ticker,
            market="US",
            price=150.0,
            currency="USD",
            source="google_finance",
        )

    def fake_yf(ticker):
        return Quote(
            ticker=ticker,
            market="US",
            price=151.0,
            currency="USD",
            source="yfinance",
            shares_outstanding=1000.0,
        )

    monkeypatch.setattr(market_data, "fetch_us_quote_google_finance", fake_google)
    monkeypatch.setattr(market_data, "fetch_us_quote_yfinance", fake_yf)

    quote = market_data.fetch_us_quote("AAPL", exchange="NASDAQ")
    assert quote.price == 150.0
    assert quote.shares_outstanding == 1000.0
    assert quote.source == "google_finance+yfinance"


def test_compute_net_cash_from_companyfacts():
    fixture = Path(__file__).resolve().parent / "fixtures" / "sec_companyfacts_sample.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    net_cash, currency = compute_net_cash_from_companyfacts(data)
    assert currency == "USD"
    assert net_cash == 3000.0


def test_get_cik_for_ticker():
    tickers = {
        "0": {"cik_str": 320193, "ticker": "AAPL"},
        "1": {"cik_str": 789019, "ticker": "MSFT"},
    }
    assert get_cik_for_ticker("AAPL", tickers) == "0000320193"
