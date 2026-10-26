"""The companies Footnote covers. Tickers only: CIKs are looked up from the SEC's
own ticker file at run time, so a typo here fails loudly instead of quietly
pulling the wrong company."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    ticker: str
    sector: str
    name: str  # what people call it; the SEC's legal name comes with each filing


COMPANIES = (
    Company("AAPL", "Technology", "Apple"),
    Company("MSFT", "Technology", "Microsoft"),
    Company("GOOGL", "Technology", "Alphabet (Google)"),
    Company("AMZN", "Consumer", "Amazon"),
    Company("NVDA", "Technology", "NVIDIA"),
    Company("META", "Technology", "Meta Platforms (Facebook)"),
    Company("TSLA", "Consumer", "Tesla"),
    Company("JPM", "Financials", "JPMorgan Chase"),
    Company("BAC", "Financials", "Bank of America"),
    Company("GS", "Financials", "Goldman Sachs"),
    Company("WMT", "Consumer", "Walmart"),
    Company("COST", "Consumer", "Costco"),
    Company("TGT", "Consumer", "Target"),
    Company("JNJ", "Healthcare", "Johnson & Johnson"),
    Company("UNH", "Healthcare", "UnitedHealth Group"),
    Company("PFE", "Healthcare", "Pfizer"),
    Company("XOM", "Energy", "Exxon Mobil"),
    Company("CVX", "Energy", "Chevron"),
    Company("KO", "Consumer", "Coca-Cola"),
    Company("DIS", "Communication", "Walt Disney"),
)

TICKERS = tuple(c.ticker for c in COMPANIES)
NAMES = {c.ticker: c.name for c in COMPANIES}
