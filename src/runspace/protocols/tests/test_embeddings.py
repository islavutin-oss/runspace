"""Embeddings adapter tests."""

from __future__ import annotations

import math

import pytest

from runspace.protocols import Embeddings, get_embeddings, reset
from runspace.protocols.config import EmbeddingsConfig
from runspace.protocols.embeddings import FixtureEmbeddings, OpenAICompatEmbeddings

# ── Common fixture ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test gets a fresh env + a reset registry cache."""
    for k in (
        "EMBEDDINGS_BACKEND",
        "EMBEDDINGS_BASE_URL",
        "EMBEDDINGS_API_KEY",
        "EMBEDDINGS_MODEL",
        "EMBEDDINGS_DIMENSIONS",
        "AI_BASE_URL",
        "AI_API_KEY",
        "APP_MODE",
    ):
        monkeypatch.delenv(k, raising=False)
    reset()
    yield
    reset()


# ── FixtureEmbeddings: contract + determinism ──────────────────────────


class TestFixtureEmbeddings:
    def test_dimensions_property(self):
        e = FixtureEmbeddings(dimensions=64)
        assert e.dimensions == 64

    def test_model_property_default(self):
        e = FixtureEmbeddings()
        assert e.model == "fixture"

    def test_embed_one_vector_length_matches_dim(self):
        e = FixtureEmbeddings(dimensions=8)
        vec = e.embed_one("hello")
        assert len(vec) == 8
        assert all(isinstance(x, float) for x in vec)

    def test_deterministic_same_input(self):
        """Same text → same vector, every call. Critical for indexing."""
        e = FixtureEmbeddings(dimensions=16)
        v1 = e.embed_one("the quick brown fox")
        v2 = e.embed_one("the quick brown fox")
        assert v1 == v2

    def test_different_inputs_different_vectors(self):
        e = FixtureEmbeddings(dimensions=16)
        v_a = e.embed_one("alpha")
        v_b = e.embed_one("beta")
        assert v_a != v_b

    def test_batch_order_preserved(self):
        e = FixtureEmbeddings(dimensions=8)
        texts = ["one", "two", "three"]
        vecs = e.embed(texts)
        assert len(vecs) == 3
        # Each row matches the single-call result.
        assert vecs[0] == e.embed_one("one")
        assert vecs[1] == e.embed_one("two")
        assert vecs[2] == e.embed_one("three")

    def test_empty_batch(self):
        assert FixtureEmbeddings().embed([]) == []

    def test_l2_normalized(self):
        """Vectors are L2-normalized so cosine == dot. Make sure we
        actually normalize — important for downstream cosine math."""
        e = FixtureEmbeddings(dimensions=32)
        vec = e.embed_one("anything")
        norm = math.sqrt(sum(x * x for x in vec))
        # Normalized vectors land within float epsilon of 1.0.
        assert abs(norm - 1.0) < 1e-9

    def test_satisfies_protocol(self):
        """Static-type smoke — FixtureEmbeddings is structurally an
        `Embeddings`. If a future change to the protocol breaks this
        assignment, the test catches it before runtime."""
        e: Embeddings = FixtureEmbeddings()
        assert e.dimensions > 0


# ── OpenAICompatEmbeddings: protocol shape (no network) ─────────────────


@pytest.mark.skipif(
    OpenAICompatEmbeddings is None,
    reason="needs the `openai` SDK — pip install 'runspace[embeddings]'",
)
class TestOpenAICompatEmbeddings:
    def test_constructs_without_network(self):
        """Constructor MUST NOT make an HTTP call. We're only validating
        wiring; live tests cover the real /v1/embeddings round-trip."""
        e = OpenAICompatEmbeddings(
            base_url="https://example.test/v1",
            api_key="fake-key",
            model="text-embedding-3-small",
            dimensions=1536,
        )
        assert e.model == "text-embedding-3-small"
        assert e.dimensions == 1536

    def test_satisfies_protocol(self):
        e: Embeddings = OpenAICompatEmbeddings(
            base_url="https://example.test/v1",
            api_key="fake-key",
            model="m",
            dimensions=4,
        )
        assert e.dimensions == 4


# ── Config resolution ──────────────────────────────────────────────────


class TestEmbeddingsConfig:
    def test_default_is_fixture(self):
        cfg = EmbeddingsConfig.from_env()
        assert cfg.backend == "fixture"
        assert cfg.dimensions == 32  # fixture default

    def test_explicit_backend_wins(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_BACKEND", "fixture")
        # Even with creds present, explicit fixture wins.
        monkeypatch.setenv("AI_BASE_URL", "https://r.example/v1")
        monkeypatch.setenv("AI_API_KEY", "k")
        cfg = EmbeddingsConfig.from_env()
        assert cfg.backend == "fixture"

    def test_app_mode_sandbox_picks_fixture(self, monkeypatch):
        monkeypatch.setenv("APP_MODE", "sandbox")
        monkeypatch.setenv("AI_BASE_URL", "https://r.example/v1")
        monkeypatch.setenv("AI_API_KEY", "k")
        cfg = EmbeddingsConfig.from_env()
        assert cfg.backend == "fixture"

    def test_autodetect_openai_via_generic_ai_vars(self, monkeypatch):
        """AI_BASE_URL / AI_API_KEY are the fallback pair.
        Auto-detect must pick those up."""
        monkeypatch.setenv("AI_BASE_URL", "https://r.example/v1")
        monkeypatch.setenv("AI_API_KEY", "k")
        cfg = EmbeddingsConfig.from_env()
        assert cfg.backend == "openai"
        assert cfg.base_url == "https://r.example/v1"
        assert cfg.api_key == "k"
        assert cfg.dimensions == 1536  # openai default

    def test_explicit_dim_overrides_default(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_DIMENSIONS", "128")
        cfg = EmbeddingsConfig.from_env()
        assert cfg.dimensions == 128

    def test_openai_backend_without_creds_raises(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")
        # No base_url, no api_key.
        with pytest.raises(RuntimeError, match="EMBEDDINGS_BACKEND=openai"):
            EmbeddingsConfig.from_env()

    def test_zero_dimensions_raises(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_DIMENSIONS", "0")
        with pytest.raises(RuntimeError, match="DIMENSIONS"):
            EmbeddingsConfig.from_env()


# ── Registry factory ───────────────────────────────────────────────────


class TestRegistry:
    def test_default_returns_fixture(self):
        e = get_embeddings()
        assert isinstance(e, FixtureEmbeddings)

    def test_caches_across_calls(self):
        a = get_embeddings()
        b = get_embeddings()
        assert a is b  # lru_cache(maxsize=1)

    @pytest.mark.skipif(
        OpenAICompatEmbeddings is None,
        reason="needs the `openai` SDK — pip install 'runspace[embeddings]'",
    )
    def test_reset_busts_cache(self, monkeypatch):
        a = get_embeddings()
        # Switch backend and reset.
        monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")
        monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://x.test/v1")
        monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
        reset()
        b = get_embeddings()
        assert a is not b
        assert isinstance(b, OpenAICompatEmbeddings)

    @pytest.mark.skipif(
        OpenAICompatEmbeddings is None,
        reason="needs the `openai` SDK — pip install 'runspace[embeddings]'",
    )
    def test_openai_backend_wires_correct_impl(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_BACKEND", "openai")
        monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://x.test/v1")
        monkeypatch.setenv("EMBEDDINGS_API_KEY", "k")
        monkeypatch.setenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
        monkeypatch.setenv("EMBEDDINGS_DIMENSIONS", "1536")
        reset()
        e = get_embeddings()
        assert isinstance(e, OpenAICompatEmbeddings)
        assert e.model == "text-embedding-3-small"
        assert e.dimensions == 1536
