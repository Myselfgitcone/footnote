"""The companies Footnote covers. Tickers only: CIKs are looked up from the SEC's
own ticker file at run time, so a typo here fails loudly instead of quietly
pulling the wrong company."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    ticker: str
    sector: str


COMPANIES = (
    Company("AAPL", "Technology"),
    Company("MSFT", "Technology"),
    Company("GOOGL", "Technology"),
    Company("AMZN", "Consumer"),
    Company("NVDA", "Technology"),
    Company("META", "Technology"),
    Company("TSLA", "Consumer"),
    Company("JPM", "Financials"),
    Company("BAC", "Financials"),
    Company("GS", "Financials"),
    Company("WMT", "Consumer"),
    Company("COST", "Consumer"),
    Company("TGT", "Consumer"),
    Company("JNJ", "Healthcare"),
    Company("UNH", "Healthcare"),
    Company("PFE", "Healthcare"),
    Company("XOM", "Energy"),
    Company("CVX", "Energy"),
    Company("KO", "Consumer"),
    Company("DIS", "Communication"),
)

TICKERS = tuple(c.ticker for c in COMPANIES)
