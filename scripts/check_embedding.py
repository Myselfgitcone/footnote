"""Run the real model from the Lambda package and check it finds the right passage.

python -I scripts/check_embedding.py build/lambda build/model
"""

import sys
import time

sys.path.insert(0, sys.argv[1])

import numpy as np

from footnote.embed import DIMENSION, OnnxEmbedder

PASSAGES = [
    "Substantially all of the Company's manufacturing is performed by outsourcing partners "
    "located primarily in China mainland, India, Japan, South Korea, Taiwan and Vietnam.",
    "The Firm's lending activities expose it to credit risk if borrowers fail to repay their loans.",
    "Our soft drink concentrates are sold to bottling partners who package and distribute the beverages.",
]
QUESTION = "Where does Apple make its products?"

embedder = OnnxEmbedder(sys.argv[2])
vectors = np.array(embedder.documents(PASSAGES))
query = np.array(embedder.query(QUESTION))
assert vectors.shape == (3, DIMENSION), vectors.shape
assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-4)
scores = vectors @ query
print("scores:", np.round(scores, 3))
assert scores.argmax() == 0, "the manufacturing passage should be closest"

texts = [PASSAGES[i % 3] * 4 for i in range(64)]  # about 300 tokens each, like a real passage
started = time.perf_counter()
embedder.documents(texts)
print(f"{len(texts) / (time.perf_counter() - started):.1f} passages/s on this machine")
