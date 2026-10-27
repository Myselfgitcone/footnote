"""Pull new filings from EDGAR into the store, raw and split into sections.

Incremental and safe to rerun: a filing that already has its clean copy is
skipped, so the daily run only does work when a company has filed something new,
and a run that dies halfway just picks up where it stopped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from footnote import facts, sections
from footnote.companies import PREDECESSORS
from footnote.edgar import Edgar, Filing
from footnote.store import Store, get_json, put_json

CATALOG = "clean/catalog.json"


def raw_key(filing: Filing) -> str:
    return f"raw/edgar/{filing.ticker}/{filing.accession}/{filing.primary_document}"


def clean_key(filing: Filing) -> str:
    return f"clean/filings/{filing.ticker}/{filing.accession}.json"


@dataclass
class Report:
    companies: int = 0
    filings_seen: int = 0
    added: list[str] = field(default_factory=list)
    skipped: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    missing_sections: dict[str, list[str]] = field(default_factory=dict)
    remaining: int = 0
    no_filings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__ | {"added_count": len(self.added), "summary": str(self)}

    def __str__(self) -> str:
        line = (
            f"{self.companies} companies, {self.filings_seen} filings: {len(self.added)} added, "
            f"{self.skipped} already had, {len(self.failed)} failed, {self.remaining} left for next run "
            f"({self.seconds:.0f}s)."
        )
        if self.missing_sections:
            gaps = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(self.missing_sections.items()))
            line += f"\nSections not found: {gaps}"
        if self.failed:
            line += "\nFailed: " + "; ".join(f"{k}: {v}" for k, v in self.failed.items())
        if self.no_filings:
            line += "\nNo filings found for: " + ", ".join(self.no_filings)
        return line


def run(
    edgar: Edgar,
    store: Store,
    tickers: list[str] | tuple[str, ...],
    forms: tuple[str, ...] = ("10-K",),
    since_year: int = 2021,
    max_new: int = 1000,
    clock=time.monotonic,
) -> Report:
    """Fetch and split up to `max_new` new filings; the rest wait for the next run.

    The cap keeps one run inside a Lambda's 15 minutes during the first backfill.
    """
    started = clock()
    report = Report()
    catalog = {entry["accession"]: entry for entry in get_json(store, CATALOG, [])}
    ciks = edgar.ciks(tickers)

    for ticker, (cik, _) in ciks.items():
        report.companies += 1
        found: dict[str, Filing] = {}
        for one in (cik, *PREDECESSORS.get(ticker, ())):
            found |= {f.accession: f for f in edgar.filings(ticker, one, forms, since_year)}
        filings = sorted(found.values(), key=lambda f: f.period)
        if not filings:
            # A company with no filings is almost always a lookup problem, not a quiet year.
            report.no_filings.append(ticker)
        report.filings_seen += len(filings)
        new = [f for f in filings if not store.exists(clean_key(f))]
        report.skipped += len(filings) - len(new)
        for filing in new:
            if len(report.added) >= max_new:
                report.remaining += 1
                continue
            try:
                entry = _ingest_one(edgar, store, filing)
            except Exception as error:  # one bad filing shouldn't stop the rest
                report.failed[filing.accession] = f"{type(error).__name__}: {error}"[:300]
                continue
            catalog[filing.accession] = entry
            report.added.append(filing.accession)
            gaps = [item for item in sections.KEEP if item not in entry["sections"] and item != "1C"]
            if gaps:
                report.missing_sections[f"{ticker} FY{filing.fiscal_year}"] = gaps
        # Each registrant's XBRL facts change with every filing; refresh them alongside.
        for one in sorted({f.cik for f in new}):
            key = facts.companyfacts_key(ticker, one, primary=one == cik)
            store.put(key, edgar.company_facts(one), "application/json")
        if new or not store.exists(facts.facts_key(ticker)):
            mine = [e for e in catalog.values() if e["ticker"] == ticker]
            if mine:
                facts.write(store, ticker, mine)

    put_json(store, CATALOG, sorted(catalog.values(), key=lambda e: (e["ticker"], e["period"])))
    report.seconds = clock() - started
    return report


def _ingest_one(edgar: Edgar, store: Store, filing: Filing) -> dict:
    key = raw_key(filing)
    if store.exists(key):
        html = store.get(key)
    else:
        html = edgar.document(filing)
        store.put(key, html, "text/html")
    parts = sections.split(html)
    meta = {
        "ticker": filing.ticker,
        "cik": filing.cik,
        "company": filing.company,
        "form": filing.form,
        "accession": filing.accession,
        "filed": filing.filed,
        "period": filing.period,
        "fiscal_year": filing.fiscal_year,
        "url": filing.url,
        "raw_key": key,
        "processed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    put_json(
        store,
        clean_key(filing),
        meta
        | {
            "sections": {
                item: {"title": s.title, "found_by": s.found_by, "text": s.text} for item, s in parts.items()
            }
        },
    )
    return meta | {"sections": {item: len(s.text) for item, s in parts.items()}}
