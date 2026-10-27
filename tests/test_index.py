import hashlib
import json

import boto3
import pytest
from moto import mock_aws

from footnote import index
from footnote.chunks import chunk_filing
from footnote.cli import filters
from footnote.ingest import CATALOG
from footnote.store import LocalStore, put_json
from tests.test_chunks import FILING


class FakeEmbedder:
    name = "fake-model"

    def __init__(self):
        self.texts = 0

    def documents(self, texts):
        self.texts += len(texts)
        return [self._vector(t) for t in texts]

    def query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255 for b in digest[:8]]


class FakeVectors:
    def __init__(self):
        self.records = {}

    def ids(self, prefix):
        return [i for i in self.records if i.startswith(prefix)]

    def delete(self, ids):
        for i in ids:
            del self.records[i]

    def upsert(self, records):
        self.records |= {r["id"]: r for r in records}

    def search(self, vector, top_k, filter=None):
        raise NotImplementedError


def filing(ticker, accession, broken=False):
    return FILING | {"ticker": ticker, "accession": accession} | ({"sections": None} if broken else {})


def setup_store(tmp_path, filings):
    store = LocalStore(tmp_path)
    for f in filings:
        put_json(store, f"clean/filings/{f['ticker']}/{f['accession']}.json", f)
    put_json(store, CATALOG, [{"ticker": f["ticker"], "accession": f["accession"]} for f in filings])
    return store


def test_indexes_every_filing_once(tmp_path):
    store = setup_store(tmp_path, [filing("AAPL", "a-24"), filing("KO", "k-24")])
    embedder, vectors = FakeEmbedder(), FakeVectors()
    first = index.run(store, embedder, vectors)
    assert sorted(first.indexed) == ["a-24", "k-24"]
    assert first.chunks == len(vectors.records) > 0
    assert json.loads(store.get("index/filings/AAPL/a-24.json"))["stamp"] == "chunker-1/fake-model"

    embedded = embedder.texts
    again = index.run(store, embedder, vectors)
    assert again.indexed == [] and again.skipped == 2
    assert embedder.texts == embedded


def test_metadata_is_what_pinecone_accepts_and_answers_cite(tmp_path):
    store = setup_store(tmp_path, [filing("AAPL", "a-24")])
    vectors = FakeVectors()
    index.run(store, FakeEmbedder(), vectors)
    record = vectors.records["a-24#1A#0000"]
    meta = record["metadata"]
    assert meta["ticker"] == "AAPL" and meta["sector"] == "Technology" and meta["fiscal_year"] == 2024
    assert meta["item"] == "1A" and meta["item_title"] == "Risk Factors"
    assert meta["heading"] == "Supply Chain Risks"
    assert meta["text"].startswith("Sentence 0 about suppliers")
    # Pinecone metadata takes strings, numbers, booleans and lists of strings; no nulls.
    for value in meta.values():
        assert isinstance(value, str | int | float | bool), value


def test_a_new_chunker_version_replaces_old_vectors_without_leaving_strays(tmp_path, monkeypatch):
    store = setup_store(tmp_path, [filing("AAPL", "a-24")])
    vectors = FakeVectors()
    index.run(store, FakeEmbedder(), vectors)
    vectors.upsert([{"id": "a-24#1A#9999", "values": [0.0], "metadata": {}}])  # from an older chunking

    monkeypatch.setattr(index, "CHUNKER_VERSION", "2")
    report = index.run(store, FakeEmbedder(), vectors)
    assert report.indexed == ["a-24"] and report.replaced == 1
    assert "a-24#1A#9999" not in vectors.records
    assert all(i.startswith("a-24#") for i in vectors.records)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SlowEmbedder(FakeEmbedder):
    """Takes a second per passage on the fake clock."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def documents(self, texts):
        self.clock.now += len(texts)
        return super().documents(texts)


def test_a_filing_that_would_not_finish_in_time_waits_for_the_next_run(tmp_path):
    store = setup_store(tmp_path, [filing("AAPL", "a-23"), filing("AAPL", "a-24"), filing("KO", "k-24")])
    passages = len(chunk_filing(FILING))
    clock = Clock()
    # Room for the first filing and a bit, not for a second one at a second per passage.
    report = index.run(store, SlowEmbedder(clock), FakeVectors(), budget_s=passages * 1.5, clock=clock)
    assert len(report.indexed) == 1 and report.remaining == 2
    assert report.chunks_per_second == pytest.approx(1.0)


def test_the_first_filing_always_starts_so_every_run_makes_progress(tmp_path):
    store = setup_store(tmp_path, [filing("AAPL", "a-24")])
    clock = Clock()
    report = index.run(store, SlowEmbedder(clock), FakeVectors(), budget_s=1, clock=clock)
    assert report.indexed == ["a-24"]


def test_one_broken_filing_does_not_stop_the_rest(tmp_path):
    store = setup_store(tmp_path, [filing("AAPL", "bad", broken=True), filing("KO", "k-24")])
    report = index.run(store, FakeEmbedder(), FakeVectors())
    assert report.indexed == ["k-24"]
    assert "bad" in report.failed
    assert not store.exists("index/filings/AAPL/bad.json")  # retried next run


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((None, None, None), None),
        ((["aapl"], None, None), {"ticker": {"$in": ["AAPL"]}}),
        (
            (["AAPL", "MSFT"], 2024, "1a"),
            {
                "$and": [
                    {"ticker": {"$in": ["AAPL", "MSFT"]}},
                    {"fiscal_year": {"$eq": 2024}},
                    {"item": {"$eq": "1A"}},
                ]
            },
        ),
    ],
)
def test_search_filters(args, expected):
    assert filters(*args) == expected


def test_lambda_handler_fetches_the_model_once_and_indexes_from_the_bucket(monkeypatch):
    from footnote import embed, handler, vectors
    from footnote.store import S3Store

    fetched, made = [], []
    shared = FakeVectors()
    monkeypatch.setattr(embed, "fetch_model", lambda store, target: fetched.append(target) or target)
    monkeypatch.setattr(embed, "OnnxEmbedder", lambda model_dir: FakeEmbedder())
    monkeypatch.setattr(
        vectors, "PineconeVectors", lambda key, dim, namespace: made.append((key, dim, namespace)) or shared
    )
    monkeypatch.setattr(handler, "_embedder", None)
    monkeypatch.setenv("PINECONE_API_KEY", "pc-test")
    monkeypatch.setenv("FOOTNOTE_BUCKET", "footnote-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "testing")

    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="footnote-test")
        store = S3Store("footnote-test")
        put_json(store, "clean/filings/AAPL/a-24.json", filing("AAPL", "a-24"))
        put_json(store, CATALOG, [{"ticker": "AAPL", "accession": "a-24"}])

        first = handler.index_handler({"budget_s": 60, "run_id": "r1"})
        again = handler.index_handler({})
        assert first["indexed_count"] == 1 and first["remaining"] == 0 and "1 indexed" in first["summary"]
        assert again["indexed_count"] == 0 and again["skipped"] == 1
        assert store.exists("index/filings/AAPL/a-24.json")
        assert json.loads(store.get("runs/footnote-index/r1.json"))["indexed_count"] == 1
    assert fetched == ["/tmp/model"]  # a warm container reuses the loaded model
    assert made[0] == ("pc-test", 384, "10k")
    assert shared.records
