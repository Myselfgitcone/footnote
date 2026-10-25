import json

import boto3
import pytest
from moto import mock_aws

from footnote import handler, ingest
from footnote.edgar import Filing
from footnote.store import LocalStore, S3Store
from tests.test_sections import TEN_K


class FakeEdgar:
    def __init__(self, filings, broken=()):
        self._filings = filings
        self.broken = set(broken)
        self.downloads = 0

    def ciks(self, tickers):
        return {t: (1000 + i, f"{t} Inc.") for i, t in enumerate(tickers)}

    def filings(self, ticker, cik, forms, since_year):
        return [f for f in self._filings if f.ticker == ticker]

    def document(self, filing):
        if filing.accession in self.broken:
            raise RuntimeError("SEC returned garbage")
        self.downloads += 1
        return TEN_K.encode()

    def company_facts(self, cik):
        return json.dumps({"cik": cik, "facts": {}}).encode()


def filing(ticker, accession, period):
    return Filing(ticker, 1000, f"{ticker} Inc.", "10-K", accession, period, period, f"{ticker.lower()}.htm")


FILINGS = [
    filing("AAPL", "a-23", "2023-09-30"),
    filing("AAPL", "a-24", "2024-09-28"),
    filing("MSFT", "m-24", "2024-06-30"),
]


def test_first_run_fetches_everything_and_a_rerun_does_nothing(tmp_path):
    store, edgar = LocalStore(tmp_path), FakeEdgar(FILINGS)
    first = ingest.run(edgar, store, ["AAPL", "MSFT"])
    assert sorted(first.added) == ["a-23", "a-24", "m-24"]
    assert store.exists("raw/edgar/AAPL/a-24/aapl.htm")
    assert store.exists("raw/edgar/AAPL/companyfacts.json")
    clean = json.loads(store.get("clean/filings/AAPL/a-24.json"))
    assert clean["fiscal_year"] == 2024
    assert clean["sections"]["1A"]["text"].startswith("Risk one")

    catalog = json.loads(store.get("clean/catalog.json"))
    assert [(e["ticker"], e["accession"]) for e in catalog] == [
        ("AAPL", "a-23"),
        ("AAPL", "a-24"),
        ("MSFT", "m-24"),
    ]
    assert catalog[0]["sections"]["1A"] > 400  # lengths only; the text lives in the filing's own file

    second = ingest.run(edgar, store, ["AAPL", "MSFT"])
    assert second.added == [] and second.skipped == 3
    assert edgar.downloads == 3


def test_a_bad_filing_is_reported_and_the_rest_still_land(tmp_path):
    report = ingest.run(FakeEdgar(FILINGS, broken={"a-23"}), LocalStore(tmp_path), ["AAPL", "MSFT"])
    assert sorted(report.added) == ["a-24", "m-24"]
    assert "a-23" in report.failed and "garbage" in report.failed["a-23"]


def test_a_capped_run_leaves_the_rest_for_next_time(tmp_path):
    store, edgar = LocalStore(tmp_path), FakeEdgar(FILINGS)
    first = ingest.run(edgar, store, ["AAPL", "MSFT"], max_new=2)
    assert len(first.added) == 2 and first.remaining == 1
    second = ingest.run(edgar, store, ["AAPL", "MSFT"], max_new=2)
    assert len(second.added) == 1 and second.remaining == 0


def test_missing_sections_are_reported(tmp_path):
    class Thin(FakeEdgar):
        def document(self, filing):
            return b"<p>Item 1. Business</p><p>" + b"word " * 200 + b"</p>"

    report = ingest.run(Thin(FILINGS[:1]), LocalStore(tmp_path), ["AAPL"])
    assert report.missing_sections == {"AAPL FY2023": ["1A", "3", "7", "7A"]}


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="footnote-test")
        yield "footnote-test"


def test_s3_store_round_trip(bucket):
    store = S3Store(bucket)
    assert not store.exists("a/b.json")
    store.put("a/b.json", b"{}", "application/json")
    store.put("a/c/d.htm", b"<p>", "text/html")
    assert store.exists("a/b.json") and store.get("a/b.json") == b"{}"
    assert store.keys("a/") == ["a/b.json", "a/c/d.htm"]


def test_lambda_handler_writes_to_the_bucket(bucket, monkeypatch):
    monkeypatch.setenv("FOOTNOTE_BUCKET", bucket)
    monkeypatch.setattr(handler, "Edgar", lambda user_agent: FakeEdgar(FILINGS))
    result = handler.ingest_handler({"tickers": ["AAPL"], "max_new": 1})
    assert result["added_count"] == 1 and result["remaining"] == 1
    assert S3Store(bucket).exists("clean/catalog.json")
