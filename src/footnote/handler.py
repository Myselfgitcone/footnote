"""AWS Lambda entry points: the daily ingest, and indexing what it brought in."""

from __future__ import annotations

import json
import logging

from footnote import index, ingest
from footnote.companies import TICKERS
from footnote.edgar import Edgar
from footnote.settings import Settings
from footnote.store import S3Store

_embedder = None  # loaded once per container, reused while it stays warm

logging.getLogger().setLevel(logging.INFO)


def ingest_handler(event: dict | None = None, context=None) -> dict:
    event = event or {}
    settings = Settings()
    report = ingest.run(
        Edgar(settings.user_agent),
        S3Store(settings.bucket),
        tickers=event.get("tickers") or TICKERS,
        forms=tuple(event.get("forms") or settings.forms),
        since_year=int(event.get("since_year") or settings.since_year),
        max_new=int(event.get("max_new") or 40),
    )
    logging.info("ingest: %s", report)
    result = report.as_dict()
    logging.info(json.dumps({k: v for k, v in result.items() if k != "added"}))
    return result


def index_handler(event: dict | None = None, context=None) -> dict:
    global _embedder
    from footnote.embed import DIMENSION, OnnxEmbedder, fetch_model
    from footnote.vectors import PineconeVectors

    event = event or {}
    settings = Settings()
    store = S3Store(settings.bucket)
    if _embedder is None:
        _embedder = OnnxEmbedder(fetch_model(store, "/tmp/model"))
    report = index.run(
        store,
        _embedder,
        PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=settings.namespace),
        tickers=event.get("tickers"),
        budget_s=float(event.get("budget_s") or 600),
    )
    logging.info("index: %s", report)
    result = report.as_dict()
    logging.info(json.dumps({k: v for k, v in result.items() if k != "indexed"}))
    return result
