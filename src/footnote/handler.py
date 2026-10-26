"""AWS Lambda entry points: the daily ingest, and indexing what it brought in.

Every run, scheduled or started by hand, leaves its result in the bucket at
runs/{function}/{run id}.json, including when it fails. A caller starts a run
without waiting on the line (a 15-minute HTTP request gets dropped by the
network well before Lambda is done) and reads the result from there instead.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime

from footnote import index, ingest
from footnote.companies import TICKERS
from footnote.edgar import Edgar
from footnote.settings import Settings
from footnote.store import S3Store, put_json

_embedder = None  # loaded once per container, reused while it stays warm

logging.getLogger().setLevel(logging.INFO)


def run_key(function: str, run_id: str) -> str:
    return f"runs/{function}/{run_id}.json"


def ingest_handler(event: dict | None = None, context=None) -> dict:
    event = event or {}
    settings = Settings()
    store = S3Store(settings.bucket)

    def work() -> dict:
        report = ingest.run(
            Edgar(settings.user_agent),
            store,
            tickers=event.get("tickers") or TICKERS,
            forms=tuple(event.get("forms") or settings.forms),
            since_year=int(event.get("since_year") or settings.since_year),
            max_new=int(event.get("max_new") or 40),
        )
        logging.info("ingest: %s", report)
        return report.as_dict()

    return _recorded(store, "footnote-ingest", event, context, work)


def index_handler(event: dict | None = None, context=None) -> dict:
    event = event or {}
    settings = Settings()
    store = S3Store(settings.bucket)

    def work() -> dict:
        global _embedder
        from footnote.embed import DIMENSION, OnnxEmbedder, fetch_model
        from footnote.vectors import PineconeVectors

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
        return report.as_dict()

    return _recorded(store, "footnote-index", event, context, work)


def _recorded(store, default_name: str, event: dict, context, work) -> dict:
    """Run `work` and write what happened, success or failure, where the caller can read it."""
    name = getattr(context, "function_name", None) or default_name
    run_id = event.get("run_id") or getattr(context, "aws_request_id", None) or _now().replace(":", "")
    started = _now()
    try:
        result = work()
    except Exception as error:
        put_json(
            store,
            run_key(name, run_id),
            {
                "run_id": run_id,
                "started_at": started,
                "finished_at": _now(),
                "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-4000:],
            },
        )
        raise
    result |= {"run_id": run_id, "started_at": started, "finished_at": _now()}
    put_json(store, run_key(name, run_id), result)
    logging.info(json.dumps({k: v for k, v in result.items() if k not in ("added", "indexed")}))
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
