"""Put the answer pipeline together from settings: the same for the API and the command line."""

from __future__ import annotations

from functools import lru_cache

from footnote import ask
from footnote.facts import facts_key
from footnote.settings import Settings
from footnote.store import Store, get_json


def asker(settings: Settings, store: Store, model_dir: str):
    from footnote.embed import DIMENSION, OnnxEmbedder
    from footnote.vectors import PineconeVectors

    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    planner, writer = ask.claude(settings.answer_model, settings.anthropic_api_key)

    @lru_cache(maxsize=32)
    def figures_for(ticker: str) -> dict:
        return get_json(store, facts_key(ticker), {})

    return ask.build_graph(
        planner,
        writer,
        OnnxEmbedder(model_dir),
        PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=settings.namespace),
        figures_for,
    )
