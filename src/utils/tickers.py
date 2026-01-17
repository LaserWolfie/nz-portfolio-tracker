import re
from typing import Optional

_PREFIX_RE = re.compile(r"^(NZX|NZE|NZ|ASX|NYSE|NASDAQ|US)\s*[:\\-]\s*", re.IGNORECASE)


def normalize_ticker(value: Optional[str]) -> str:
    """
    Normalize tickers so comparisons work across formats:
    - 'NZ:NZK', 'NZE:NZK', 'NZX:NZK' -> 'NZK'
    - 'NZK.NZ' -> 'NZK'
    - trims whitespace, uppercases
    """
    if value is None:
        return ""
    s = str(value).strip().upper()

    # remove common exchange prefixes
    s = _PREFIX_RE.sub("", s)

    # remove common suffixes
    for suf in (".NZ", ".AX", ".TO", ".L"):
        if s.endswith(suf):
            s = s[: -len(suf)]

    # collapse whitespace
    s = re.sub(r"\s+", "", s)
    return s
