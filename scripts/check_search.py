"""Ask the index a question whose answer is known, and check the answer's passage comes back.

Apple's risk factors say where its products are made. Fresh vectors take a few
seconds to become searchable, so this waits for them.
"""

import sys
import time

from footnote.embed import DIMENSION, OnnxEmbedder
from footnote.settings import Settings
from footnote.vectors import PineconeVectors

QUESTION = "Where are Apple's products manufactured?"
EXPECTED = "outsourcing partners"

settings = Settings()
vectors = PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=settings.namespace)
query = OnnxEmbedder(settings.model_dir).query(QUESTION)

hits: list[dict] = []
for _ in range(30):
    hits = vectors.search(query, 5, {"ticker": {"$eq": "AAPL"}})
    if hits:
        break
    time.sleep(2)
print(f"{vectors.count()} vectors in namespace {settings.namespace}")
for hit in hits:
    where = f"FY{hit['fiscal_year']} Item {hit['item']} > {hit['heading']}"
    print(f"[{hit['score']:.3f}] {where}: {hit['text'][:160]}")
if not any(EXPECTED in hit["text"] for hit in hits):
    sys.exit(f"no passage mentioning {EXPECTED!r} in the top 5 for {QUESTION!r}")
