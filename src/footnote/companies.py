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
    # Earlier SEC registrants whose filings are this company's history. When a
    # company reorganizes under a new parent, the ticker moves to the parent's
    # CIK, but every 10-K until then was filed under the old one.
    predecessor_ciks: tuple[int, ...] = ()


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
    Company(
        "XOM",
        "Energy",
        "Exxon Mobil",
        # ExxonMobil Holdings Corp became the successor issuer in July 2026 (form 8-K12B);
        # Exxon Mobil Corp filed every 10-K before that.
        predecessor_ciks=(34088,),
    ),
    Company("CVX", "Energy", "Chevron"),
    Company("KO", "Consumer", "Coca-Cola"),
    Company("DIS", "Communication", "Walt Disney"),
)

TICKERS = tuple(c.ticker for c in COMPANIES)
NAMES = {c.ticker: c.name for c in COMPANIES}
PREDECESSORS = {c.ticker: c.predecessor_ciks for c in COMPANIES}
