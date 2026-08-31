"""OpenAI-compatible Embeddings — wraps any /v1/embeddings endpoint.

Router, OpenAI direct, vLLM, Together, and most local serving
stacks expose the same wire format. One impl covers all of them; the
caller picks `base_url` + `api_key` via config.
"""

from __future__ import annotations

from openai import OpenAI


class OpenAICompatEmbeddings:
    """Concrete Embeddings impl over an OpenAI-compatible HTTP API.

    Construct with the explicit base_url/api_key/model — the registry
    factory pulls those out of `EmbeddingsConfig`. Keep the constructor
    signature small and deterministic so tests can inject fakes.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
    ):
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model
        self._dim = dimensions

    @property
    def dimensions(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in out.data]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
