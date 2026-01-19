from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.nzx_instruments import get_nzx_snapshot


def _format(value):
    if value is None:
        return "None"
    return str(value)


def main() -> int:
    tickers = [arg.strip().upper() for arg in sys.argv[1:] if arg.strip()]
    if not tickers:
        print("Usage: python scripts/nzx_smoketest.py EBO NZK IFT")
        return 1
    for ticker in tickers:
        try:
            snapshot = get_nzx_snapshot(ticker)
        except Exception as exc:
            print(f"{ticker}: ERROR {exc}")
            continue
        print(
            f"{ticker} | Company={_format(snapshot.get('company'))} | "
            f"Shares_bn={_format(snapshot.get('shares_bn'))} | "
            f"URL={_format(snapshot.get('source_url'))} | "
            f"Company_source={_format(snapshot.get('company_source'))} | "
            f"Shares_source={_format(snapshot.get('shares_source'))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
