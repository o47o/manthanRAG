"""Embedding model swap (Part B, docs/EMBEDDING_MODEL_SWAP.md).

The e5 family silently degrades without its literal prefixes. These tests pin the
prefixes on every embed site (engine passages + queries, backend memories/CoT/wiki
matcher, ARIA intent retriever) and the model identity across all three code bases,
without loading any model.
"""
import os
import re

import numpy as np
import pytest

from app.config import settings
from app.services import embedding_service as es

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_BACKEND_COT = os.path.join(_REPO, "backend", "app", "agents", "cot_retriever.py")
_ARIA_RET = os.path.join(_REPO, "Agents", "IntentClassifier", "core", "rag_retriever.py")
_ARIA_SET = os.path.join(_REPO, "Agents", "IntentClassifier", "config", "settings.py")
_BACKEND_ENV = os.path.join(_REPO, "backend", ".env")

EXPECTED_MODEL = "intfloat/e5-large-v2"


class _FakeModel:
    """Captures what the service passes to encode()."""
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kw):
        self.calls.append((texts, kw))
        n = len(texts) if isinstance(texts, list) else 1
        out = np.zeros((n, 1024), dtype=np.float32)
        return out if isinstance(texts, list) else out[0]


@pytest.fixture
def fake_model(monkeypatch):
    fm = _FakeModel()
    monkeypatch.setattr(es, "_model", fm)
    monkeypatch.setattr(settings, "RETRIEVAL_CACHE_ENABLED", False, raising=False)
    return fm


def test_prefix_constants_are_e5():
    assert es.PASSAGE_PREFIX == "passage: "
    assert es.QUERY_PREFIX == "query: "


def test_engine_default_model_is_e5():
    assert settings.BGE_MODEL_NAME == EXPECTED_MODEL
    assert es.model_name() == EXPECTED_MODEL


def test_embed_passages_applies_passage_prefix(fake_model):
    es.embed_passages(["alpha", "beta"])
    texts, kw = fake_model.calls[-1]
    assert texts == ["passage: alpha", "passage: beta"]
    assert kw.get("normalize_embeddings") is True


def test_embed_query_applies_query_prefix(fake_model):
    es.embed_query("what is otd")
    texts, kw = fake_model.calls[-1]
    assert texts == "query: what is otd"
    assert kw.get("normalize_embeddings") is True


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_backend_cot_retriever_prefixes_and_model():
    src = _read(_BACKEND_COT)
    assert '"query: "' in src, "backend embedder must prefix with 'query: '"
    assert EXPECTED_MODEL in src
    code = re.sub(r"#.*", "", src.split('"""', 2)[-1])   # drop module docstring + comments
    assert "BAAI" not in code, "backend embedder still hard-codes BGE"


def test_aria_retriever_prefixes_and_model():
    src = _read(_ARIA_RET)
    assert '"query: "' in src
    assert 'SentenceTransformer("BAAI' not in src
    assert EXPECTED_MODEL in _read(_ARIA_SET)


def test_env_points_every_consumer_at_the_same_model():
    env = _read(_BACKEND_ENV)
    keys = {}
    for line in env.splitlines():
        m = re.match(r"^(BGE_MODEL_NAME|EMBEDDING_MODEL|EMBEDDER_MODEL)=(.+)$", line.strip())
        if m:
            keys[m.group(1)] = m.group(2).strip()
    assert set(keys) == {"BGE_MODEL_NAME", "EMBEDDING_MODEL", "EMBEDDER_MODEL"}, keys
    assert len(set(keys.values())) == 1, f"embedding model keys disagree: {keys}"
    assert next(iter(keys.values())) == EXPECTED_MODEL
