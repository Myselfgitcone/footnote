"""AWS Lambda entry point for the daily ingest."""

from __future__ import annotations

import json
import logging

from footnote import ingest
from footnote.companies import TICKERS
from footnote.edgar import Edgar
from footnote.settings import Settings
from footnote.store import S3Store

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
