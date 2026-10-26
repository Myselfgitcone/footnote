"""Put every clean filing's passages into Pinecone.

Incremental like ingest: each indexed filing gets a marker in the store saying
which chunker version and model made its vectors. A filing whose marker is
missing or out of date is (re)indexed: new vectors are written first, then any
of its old ones that no longer exist are deleted, so search never sees a gap.

Embedding is the slow part, so a run stops starting new filings once its time
budget is spent and reports how many are left, the same way ingest does.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from footnote.chunks import Chunk, chunk_filing
from footnote.companies import COMPANIES
from footnote.ingest import CATALOG
from footnote.store import Store, get_json, put_json
from footnote.vectors import Vectors

if TYPE_CHECKING:  # numpy and the model stay out of the ingest Lambda's imports
    from footnote.embed import Embedder

# Bump when chunking changes in a way that should rebuild every filing's vectors.
CHUNKER_VERSION = "1"
SECTORS = {c.ticker: c.sector for c in COMPANIES}


def marker_key(ticker: str, accession: str) -> str:
    return f"index/filings/{ticker}/{accession}.json"


@dataclass
class IndexReport:
    filings: int = 0
    indexed: list[str] = field(default_factory=list)
    skipped: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    remaining: int = 0
    chunks: int = 0
    replaced: int = 0
    embed_seconds: float = 0.0
    seconds: float = 0.0

    @property
    def chunks_per_second(self) -> float:
        return self.chunks / self.embed_seconds if self.embed_seconds else 0.0

    def as_dict(self) -> dict:
        return self.__dict__ | {
            "indexed_count": len(self.indexed),
            "chunks_per_second": round(self.chunks_per_second, 1),
            "summary": str(self),
        }

    def __str__(self) -> str:
        line = (
            f"{self.filings} filings: {len(self.indexed)} indexed ({self.chunks} passages, "
            f"{self.chunks_per_second:.1f}/s embedding), {self.skipped} up to date, "
            f"{len(self.failed)} failed, {self.remaining} left for next run ({self.seconds:.0f}s)."
        )
        if self.failed:
            line += "\nFailed: " + "; ".join(f"{k}: {v}" for k, v in self.failed.items())
        return line


def run(
    store: Store,
    embedder: Embedder,
    vectors: Vectors,
    tickers: list[str] | tuple[str, ...] | None = None,
    budget_s: float = 600,
    clock=time.monotonic,
) -> IndexReport:
    started = clock()
    report = IndexReport()
    stamp = f"chunker-{CHUNKER_VERSION}/{embedder.name}"
    for entry in get_json(store, CATALOG, []):
        if tickers and entry["ticker"] not in tickers:
            continue
        report.filings += 1
        marker = get_json(store, marker_key(entry["ticker"], entry["accession"]))
        if marker and marker.get("stamp") == stamp:
            report.skipped += 1
            continue
        if clock() - started > budget_s:
            report.remaining += 1
            continue
        try:
            _index_one(store, embedder, vectors, entry, stamp, report, clock)
        except Exception as error:  # one bad filing shouldn't stop the rest
            report.failed[entry["accession"]] = f"{type(error).__name__}: {error}"[:300]
    report.seconds = clock() - started
    return report


def _index_one(store, embedder, vectors, entry, stamp, report, clock) -> None:
    filing = get_json(store, f"clean/filings/{entry['ticker']}/{entry['accession']}.json")
    chunks = chunk_filing(filing)
    began = clock()
    values = embedder.documents([c.for_embedding for c in chunks])
    report.embed_seconds += clock() - began

    previous = set(vectors.ids(prefix=f"{entry['accession']}#"))
    vectors.upsert([record(filing, c, v) for c, v in zip(chunks, values, strict=True)])
    stale = sorted(previous - {c.id for c in chunks})
    if stale:
        vectors.delete(stale)
    put_json(
        store,
        marker_key(entry["ticker"], entry["accession"]),
        {
            "stamp": stamp,
            "chunks": len(chunks),
            "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    report.indexed.append(entry["accession"])
    report.chunks += len(chunks)
    report.replaced += len(stale)


def record(filing: dict, chunk: Chunk, values: list[float]) -> dict:
    """One Pinecone vector. Metadata is what search filters on and what answers cite."""
    return {
        "id": chunk.id,
        "values": values,
        "metadata": {
            "ticker": filing["ticker"],
            "company": filing["company"],
            "sector": SECTORS.get(filing["ticker"], "Other"),
            "form": filing["form"],
            "fiscal_year": filing["fiscal_year"],
            "period": filing["period"],
            "filed": filing["filed"],
            "accession": filing["accession"],
            "url": filing["url"],
            "item": chunk.item,
            "item_title": filing["sections"][chunk.item]["title"],
            "heading": chunk.heading,
            "position": chunk.position,
            "text": chunk.text,
        },
    }
