"""Text to vectors with BAAI's bge-small-en-v1.5, run on CPU with ONNX Runtime.

Why local and not Pinecone's hosted embedding: the backfill alone is about ten
million tokens, twice the free monthly allowance, and every re-chunking would
pay it again. bge-small runs in a Lambda with no GPU and no PyTorch, and the
same file embeds questions later, so passages and questions always share a model.

The model is the published ONNX export. It is copied once into the project's
bucket and never replaced there, so a change upstream can't quietly put new
vectors next to old ones that no longer match.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"
DIMENSION = 384
MAX_TOKENS = 512
# bge's instruction for the question side of retrieval; passages are embedded as is.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MODEL_FILES = ("model.onnx", "tokenizer.json")
MODEL_PREFIX = "models/bge-small-en-v1.5/"


class Embedder(Protocol):
    name: str

    def documents(self, texts: list[str]) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


class OnnxEmbedder:
    name = MODEL

    def __init__(self, model_dir: str | Path, batch_size: int = 32, threads: int | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or os.cpu_count() or 1
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.batch_size = batch_size

    def documents(self, texts: list[str]) -> list[list[float]]:
        # Similar lengths batched together waste less work on padding.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors: list[list[float]] = [[] for _ in texts]
        for start in range(0, len(order), self.batch_size):
            batch = order[start : start + self.batch_size]
            for i, vector in zip(batch, self._embed([texts[i] for i in batch]), strict=True):
                vectors[i] = vector
        return vectors

    def query(self, text: str) -> list[float]:
        return self._embed([QUERY_INSTRUCTION + text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        encodings = self.tokenizer.encode_batch(texts)
        feed = {
            "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in encodings], dtype=np.int64),
        }
        hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
        cls = hidden[:, 0]  # bge pools with the [CLS] token, then normalizes
        cls = cls / np.linalg.norm(cls, axis=1, keepdims=True)
        return cls.astype(np.float32).tolist()


def fetch_model(store, target: str | Path) -> Path:
    """Copy the model from the bucket to local disk, once per Lambda container."""
    target = Path(target)
    for name in MODEL_FILES:
        path = target / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".part")
            partial.write_bytes(store.get(MODEL_PREFIX + name))
            partial.rename(path)
    return target
