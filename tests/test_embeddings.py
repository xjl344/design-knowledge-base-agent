import sys
import types

import src.embeddings as embeddings
from src.embeddings import resolve_embedding_device


def test_auto_prefers_cuda_when_available(monkeypatch):
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True),
        __version__="0.0",
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert resolve_embedding_device("auto") == "cuda"


def test_auto_falls_back_to_cpu_when_cuda_is_unavailable(monkeypatch):
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        __version__="0.0",
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert resolve_embedding_device("auto") == "cpu"


def test_get_embeddings_forwards_requested_device(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(embeddings, "resolve_embedding_device", lambda configured: configured or "unset")

    def fake_build(device, batch_size):
        captured["device"] = device
        captured["batch_size"] = batch_size
        return object()

    monkeypatch.setattr(embeddings, "_build_embeddings", fake_build)
    embeddings.get_embeddings(device="cpu")
    assert captured["device"] == "cpu"
    assert captured["batch_size"] == max(1, embeddings.settings.embedding_batch_size)


def test_get_embeddings_defaults_to_configured_embedding_device(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(embeddings, "resolve_embedding_device", lambda configured: configured or "unset")

    def fake_build(device, batch_size):
        captured["device"] = device
        captured["batch_size"] = batch_size
        return object()

    monkeypatch.setattr(embeddings, "_build_embeddings", fake_build)
    embeddings.get_embeddings()
    assert captured["device"] == embeddings.settings.embedding_device
    assert captured["batch_size"] == max(1, embeddings.settings.embedding_batch_size)


def test_build_embeddings_does_not_duplicate_progress_argument(monkeypatch):
    captured: dict[str, object] = {}

    class FakeHuggingFaceEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._client = types.SimpleNamespace(max_seq_length=8192)

    monkeypatch.setattr(embeddings, "HuggingFaceEmbeddings", FakeHuggingFaceEmbeddings)
    monkeypatch.setattr(
        type(embeddings.settings), "validate_embedding_model", lambda self: None
    )

    embeddings._build_embeddings.cache_clear()
    result = embeddings._build_embeddings("cpu", 1)

    assert result._client.max_seq_length == 1024
    assert captured["encode_kwargs"] == {
        "normalize_embeddings": True,
        "batch_size": 1,
    }
