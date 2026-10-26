"""The Pinecone side: one serverless index, passages in a namespace.

Each vector's metadata holds the passage text and where it came from, so a
search result can be quoted and cited without a second lookup. IDs start with
the filing's accession number, which lets a re-indexed filing replace exactly
its own passages.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

INDEX = "footnote"
NAMESPACE = "10k"
CLOUD, REGION = "aws", "us-east-1"  # the only region Pinecone's free plan offers
UPSERT_BATCH = 100


class Vectors(Protocol):
    def ids(self, prefix: str) -> list[str]: ...
    def delete(self, ids: list[str]) -> None: ...
    def upsert(self, records: list[dict[str, Any]]) -> None: ...
    def search(self, vector: list[float], top_k: int, filter: dict | None = None) -> list[dict]: ...


class PineconeVectors:
    def __init__(self, api_key: str, dimension: int, index: str = INDEX, namespace: str = NAMESPACE):
        from pinecone import Pinecone, ServerlessSpec

        if not api_key:
            raise ValueError("PINECONE_API_KEY is not set")
        client = Pinecone(api_key=api_key)
        if not client.has_index(index):
            client.create_index(
                name=index,
                dimension=dimension,
                metric="cosine",
                spec=ServerlessSpec(cloud=CLOUD, region=REGION),
                tags={"project": "footnote"},
            )  # waits until the index is ready
        description = client.describe_index(index)
        if description.dimension != dimension:
            raise ValueError(
                f"index {index} has dimension {description.dimension}, the model makes {dimension}"
            )
        self.index = client.Index(host=description.host)
        self.namespace = namespace

    def ids(self, prefix: str) -> list[str]:
        found: list[str] = []
        for page in self.index.list(prefix=prefix, namespace=self.namespace):
            found += [item.id for item in page.vectors if item.id]
        return found

    def delete(self, ids: list[str]) -> None:
        for start in range(0, len(ids), 1000):
            self.index.delete(ids=ids[start : start + 1000], namespace=self.namespace)

    def upsert(self, records: list[dict[str, Any]]) -> None:
        for batch in _batches(records, UPSERT_BATCH):
            self.index.upsert(vectors=batch, namespace=self.namespace, show_progress=False)

    def search(self, vector: list[float], top_k: int, filter: dict | None = None) -> list[dict]:
        response = self.index.query(
            vector=vector, top_k=top_k, filter=filter, namespace=self.namespace, include_metadata=True
        )
        return [{"id": m.id, "score": m.score, **(m.metadata or {})} for m in response.matches]

    def count(self) -> int:
        stats = self.index.describe_index_stats()
        summary = stats.namespaces.get(self.namespace)
        return summary.vector_count if summary else 0

    def drop_namespace(self) -> None:
        self.index.delete(delete_all=True, namespace=self.namespace)


def _batches(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
