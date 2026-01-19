from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.sec_fundamentals import SEC_USER_AGENT_HELP, fetch_us_fundamentals, get_sec_headers, get_sec_user_agent


def _format(value):
    if value is None:
        return "None"
    return str(value)


def _redact_user_agent(user_agent: str) -> str:
    if "@" not in user_agent:
        return user_agent
    name, domain = user_agent.split("@", 1)
    redacted_domain = "***"
    if "." in domain:
        redacted_domain = "***." + domain.split(".")[-1]
    return f"{name}@{redacted_domain}"


def main() -> int:
    tickers = [arg.strip().upper() for arg in sys.argv[1:] if arg.strip()]
    if not tickers:
        print("Usage: python scripts/sec_fcf_smoketest.py MSFT PYPL")
        return 1

    user_agent = get_sec_user_agent()
    if not user_agent:
        print(SEC_USER_AGENT_HELP)
        return 1
    print(f"Using SEC User-Agent: {_redact_user_agent(user_agent)}")
    headers = get_sec_headers(user_agent)
    for ticker in tickers:
        try:
            data = fetch_us_fundamentals(ticker, headers)
        except Exception as exc:
            print(f"{ticker}: ERROR {exc}")
            continue
        ocf = data.get("ocf")
        capex = data.get("capex")
        fcf = None if ocf is None or capex is None else ocf - capex
        print(
            f"{ticker} | CIK={_format(data.get('cik'))} | "
            f"OCF={_format(ocf)} | Capex={_format(capex)} | "
            f"FCF={_format(fcf)} | FCF_bn={_format(data.get('fcf_bn'))} | "
            f"NetCash_bn={_format(data.get('net_cash_bn'))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
