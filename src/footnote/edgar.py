"""A small, polite client for SEC EDGAR.

EDGAR's fair-access rules: identify yourself in the User-Agent, and stay under
10 requests a second. This client does both, and retries when the SEC asks it
to slow down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DATA = "https://data.sec.gov"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Filing:
    ticker: str
    cik: int
    company: str
    form: str
    accession: str  # 0000320193-24-000123
    filed: str  # 2024-11-01
    period: str  # 2024-09-28, the period the report covers
    primary_document: str

    @property
    def fiscal_year(self) -> int:
        """The year the reporting period ends in. Walmart's year to January 2025 is its fiscal 2025."""
        return int(self.period[:4])

    @property
    def url(self) -> str:
        return f"{ARCHIVES}/{self.cik}/{self.accession.replace('-', '')}/{self.primary_document}"


class Edgar:
    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_interval_s: float = 0.12,
        sleep=time.sleep,
    ):
        if "@" not in user_agent:
            raise ValueError("the SEC asks for a contact email in the User-Agent")
        self.client = client or httpx.Client(timeout=60, follow_redirects=True)
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self.min_interval_s = min_interval_s
        self.sleep = sleep
        self._last = 0.0

    def get(self, url: str, attempts: int = 5) -> httpx.Response:
        for attempt in range(1, attempts + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            response = self.client.get(url, headers=self.headers)
            if response.status_code < 300:
                return response
            if response.status_code in RETRY_STATUS and attempt < attempts:
                self.sleep(2**attempt)
                continue
            response.raise_for_status()
        raise AssertionError("unreachable")

    def ciks(self, tickers: list[str] | tuple[str, ...]) -> dict[str, tuple[int, str]]:
        """Ticker -> (CIK, company name), from the SEC's own list. Unknown tickers fail loudly."""
        listing = self.get(TICKERS_URL).json()
        by_ticker = {row["ticker"].upper(): (int(row["cik_str"]), row["title"]) for row in listing.values()}
        missing = [t for t in tickers if t.upper() not in by_ticker]
        if missing:
            raise KeyError(f"not in the SEC's ticker list: {', '.join(missing)}")
        return {t: by_ticker[t.upper()] for t in tickers}

    def filings(self, ticker: str, cik: int, forms: tuple[str, ...], since_year: int) -> list[Filing]:
        """Every filing of the given forms whose period ends in `since_year` or later.

        The submissions file only holds the most recent filings inline; older ones
        are in extra pages. A bank that files thousands of prospectuses a year can
        push its 10-Ks out of the recent list within months, so the pages matter.
        """
        submissions = self.get(f"{DATA}/submissions/CIK{cik:010d}.json").json()
        company = submissions.get("name", ticker)
        pages = [submissions["filings"]["recent"]]
        for extra in submissions["filings"].get("files", []):
            if extra.get("filingTo", "9999") >= f"{since_year}-01-01":
                pages.append(self.get(f"{DATA}/submissions/{extra['name']}").json())

        found = {}
        for page in pages:
            for row in _rows(page):
                if row["form"] not in forms or not row.get("reportDate") or not row.get("primaryDocument"):
                    continue
                filing = Filing(
                    ticker=ticker,
                    cik=cik,
                    company=company,
                    form=row["form"],
                    accession=row["accessionNumber"],
                    filed=row["filingDate"],
                    period=row["reportDate"],
                    primary_document=row["primaryDocument"],
                )
                if filing.fiscal_year >= since_year:
                    found[filing.accession] = filing
        return sorted(found.values(), key=lambda f: f.period)

    def document(self, filing: Filing) -> bytes:
        return self.get(filing.url).content

    def company_facts(self, cik: int) -> bytes:
        """Every XBRL fact the company has reported: the official numbers answers get checked against."""
        return self.get(f"{DATA}/api/xbrl/companyfacts/CIK{cik:010d}.json").content


def _rows(columns: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """EDGAR sends filings as parallel arrays; turn them into one dict per filing."""
    keys = [k for k, v in columns.items() if isinstance(v, list)]
    return [dict(zip(keys, values, strict=True)) for values in zip(*(columns[k] for k in keys), strict=True)]
