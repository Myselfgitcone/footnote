"""The embedder's plumbing, against a tiny stand-in model built here.

The stand-in has bge-small's inputs and output shape: token ids in, one 384-wide
vector per token out (here just a lookup table). It checks tokenizing, batching,
[CLS] pooling and normalizing without downloading the real model. CI's package
job runs the real one.
"""

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors

from footnote.embed import DIMENSION, MODEL_PREFIX, QUERY_INSTRUCTION, OnnxEmbedder, fetch_model
from footnote.store import LocalStore

WORDS = (
    "apple makes phones in china banks lend money risk factors "
    "represent this sentence for searching relevant passages"
).split()


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("model")
    vocab = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3} | {w: i + 4 for i, w in enumerate(WORDS)}
    tokenizer = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    tokenizer.normalizer = normalizers.BertNormalizer(lowercase=True)
    tokenizer.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    tokenizer.save(str(folder / "tokenizer.json"))

    table = np.random.default_rng(0).normal(size=(len(vocab), DIMENSION)).astype(np.float32)
    # Like attention, mix the sentence into every position, ignoring padding: each
    # token's vector plus the masked mean of the sentence's. Without this, [CLS] would
    # come out the same for every input.
    graph = helper.make_graph(
        [
            helper.make_node("Gather", ["table", "input_ids"], ["tokens"]),
            helper.make_node("Cast", ["attention_mask"], ["mask_f"], to=TensorProto.FLOAT),
            helper.make_node("Unsqueeze", ["mask_f", "last"], ["mask3"]),
            helper.make_node("Mul", ["tokens", "mask3"], ["masked"]),
            helper.make_node("ReduceSum", ["masked", "axis"], ["total"], keepdims=1),
            helper.make_node("ReduceSum", ["mask3", "axis"], ["count"], keepdims=1),
            helper.make_node("Div", ["total", "count"], ["mean"]),
            helper.make_node("Add", ["tokens", "mean"], ["last_hidden_state"]),
        ],
        "stand_in",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "seq"]),
            helper.make_tensor_value_info("token_type_ids", TensorProto.INT64, ["batch", "seq"]),
        ],
        [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["batch", "seq", DIMENSION])],
        initializer=[
            numpy_helper.from_array(table, "table"),
            numpy_helper.from_array(np.array([1]), "axis"),
            numpy_helper.from_array(np.array([2]), "last"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.save(model, str(folder / "model.onnx"))
    return folder


def test_vectors_are_unit_length_and_384_wide(model_dir):
    vectors = OnnxEmbedder(model_dir).documents(["apple makes phones", "banks lend money"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == DIMENSION
        assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)


def test_batching_keeps_the_input_order(model_dir):
    texts = ["risk", "apple makes phones in china", "banks", "money banks lend", "factors"]
    one_at_a_time = [OnnxEmbedder(model_dir).documents([t])[0] for t in texts]
    batched = OnnxEmbedder(model_dir, batch_size=2).documents(texts)
    np.testing.assert_allclose(batched, one_at_a_time, atol=1e-5)


def test_questions_get_the_bge_instruction(model_dir):
    embedder = OnnxEmbedder(model_dir)
    np.testing.assert_allclose(
        embedder.query("risk factors"), embedder.documents([QUERY_INSTRUCTION + "risk factors"])[0], atol=1e-5
    )
    assert not np.allclose(embedder.query("risk factors"), embedder.documents(["risk factors"])[0])


def test_model_is_copied_from_the_store_once(model_dir, tmp_path):
    store = LocalStore(tmp_path / "bucket")
    for name in ("model.onnx", "tokenizer.json"):
        store.put(MODEL_PREFIX + name, (model_dir / name).read_bytes())
    local = fetch_model(store, tmp_path / "tmp-model")
    assert (local / "model.onnx").read_bytes() == (model_dir / "model.onnx").read_bytes()
    store.put(MODEL_PREFIX + "model.onnx", b"changed")
    fetch_model(store, local)  # already there: not fetched again
    assert (local / "model.onnx").read_bytes() == (model_dir / "model.onnx").read_bytes()
    assert OnnxEmbedder(local).query("apple")
