"""The HTTP API: POST /ask with a question, get back a cited answer.

Runs in a Lambda behind a function URL (through Mangum), or locally with
`uvicorn footnote.api:app`. Every question costs a Claude call, so answers are
cached by question for a day and the number of fresh answers per day is capped.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from footnote.settings import Settings
from footnote.store import LocalStore, S3Store, Store, get_json, put_json

log = logging.getLogger("footnote.api")
app = FastAPI(title="Footnote", description="Answers about SEC 10-K filings that cite their source.")
_state: dict = {}
# Part of every cache key: bump it to retire cached answers after the data or the prompts change.
CACHE_VERSION = "3"


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=300)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/ask")
def ask_question(body: Question) -> dict:
    settings, store = _settings(), _store()
    question = " ".join(body.question.split())
    digest = hashlib.sha256(f"{CACHE_VERSION}:{question.lower()}".encode()).hexdigest()[:32]
    cache_key = f"ask/cache/{digest}.json"
    cached = get_json(store, cache_key)
    if cached and time.time() - cached["answered_at"] < 24 * 3600:
        return cached | {"cached": True}

    today = datetime.now(UTC).date().isoformat()
    usage_key = f"ask/usage/{today}.json"
    used = get_json(store, usage_key, {"count": 0})["count"]
    if used >= settings.daily_question_limit:
        raise HTTPException(429, "Footnote has answered its limit of new questions for today. Try tomorrow.")

    started = time.perf_counter()
    try:
        result = _asker().invoke({"question": question})
    except Exception:
        log.exception("answering failed")
        raise HTTPException(502, "Something went wrong answering that. Please try again.") from None
    finally:
        _flush_traces()
    response = {
        "question": question,
        "answer": result["answer"],
        "sources": [_public(s) for s in result.get("sources", [])],
        "plan": result["plan"].model_dump(),
        "seconds": round(time.perf_counter() - started, 1),
        "answered_at": time.time(),
    }
    put_json(store, usage_key, {"count": used + 1})  # close enough under light, rare concurrency
    if response["sources"]:
        # An answer that found nothing may be a gap in the data that gets fixed; don't hold on to it.
        put_json(store, cache_key, response)
    return response | {"cached": False}


def _public(source: dict) -> dict:
    keep = (
        "id kind ticker company fiscal_year item item_title heading text url score "
        "metric label value unit concept concept_label accession"
    ).split()
    return {k: source[k] for k in keep if k in source}


def _settings() -> Settings:
    if "settings" not in _state:
        _state["settings"] = Settings()
    return _state["settings"]


def _store() -> Store:
    if "store" not in _state:
        settings = _settings()
        _state["store"] = S3Store(settings.bucket) if settings.bucket else LocalStore(settings.local_dir)
    return _state["store"]


def _asker():
    if "asker" not in _state:
        from footnote.embed import fetch_model
        from footnote.service import asker

        settings, store = _settings(), _store()
        model_dir = fetch_model(store, "/tmp/model") if settings.bucket else settings.model_dir
        _state["asker"] = asker(settings, store, str(model_dir))
    return _state["asker"]


def _flush_traces() -> None:
    """Send LangSmith traces before Lambda freezes the container between requests."""
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception:  # tracing must never break an answer
        log.warning("couldn't flush traces", exc_info=True)


def _make_handler():
    from mangum import Mangum

    return Mangum(app, lifespan="off")


handler = _make_handler()
