"""Put the answer pipeline together from settings: the same for the API and the command line."""

from __future__ import annotations

import time

from footnote import ask
from footnote.facts import facts_key
from footnote.settings import Settings
from footnote.store import Store, get_json

FIGURES_TTL_S = 3600


def asker(settings: Settings, store: Store, model_dir: str):
    from footnote.embed import DIMENSION, OnnxEmbedder
    from footnote.vectors import PineconeVectors

    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    planner, writer = ask.claude(settings.answer_model, settings.anthropic_api_key)

    return ask.build_graph(
        planner,
        writer,
        OnnxEmbedder(model_dir),
        PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=settings.namespace),
        figures_reader(store),
    )


def figures_reader(store: Store, clock=time.monotonic):
    """A company's figures, kept for an hour. A missing file isn't remembered, so
    figures written after the container started still show up."""
    cache: dict[str, tuple[float, dict]] = {}

    def figures_for(ticker: str) -> dict:
        held = cache.get(ticker)
        if held and clock() - held[0] < FIGURES_TTL_S:
            return held[1]
        figures = get_json(store, facts_key(ticker), {})
        if figures.get("years"):
            cache[ticker] = (clock(), figures)
        return figures

    return figures_for
