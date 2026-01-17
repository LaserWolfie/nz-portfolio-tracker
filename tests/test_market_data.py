from src.services import market_data
from src.services.market_data import Quote


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
