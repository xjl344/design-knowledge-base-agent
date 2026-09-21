from langchain_core.documents import Document
from pathlib import Path
from types import SimpleNamespace

import src.reranker as reranker_module
from src.reranker import RerankOutcome, rerank


def _settings(**overrides):
    values = dict(
        reranker_enabled=True,
        reranker_model_path=Path("model"),
        reranker_device="auto",
        reranker_max_length=1024,
        reranker_use_fp16=False,
        reranker_batch_size=8,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disabled_reranker_is_observable(monkeypatch):
    monkeypatch.setattr(reranker_module, "settings", _settings(reranker_enabled=False))
    outcome = rerank("问题", [Document(page_content="证据")])
    assert isinstance(outcome, RerankOutcome)
    assert outcome.status == "disabled"
    assert outcome.fallback_reason == "RERANKER_ENABLED=false"


def test_cuda_failure_retries_cpu(monkeypatch):
    monkeypatch.setattr(reranker_module, "settings", _settings())
    monkeypatch.setattr(reranker_module, "_cuda_available", lambda: True)
    calls = []

    def fake_predict(question, documents, device):
        calls.append(device)
        if device == "cuda":
            raise RuntimeError("out of memory")
        return documents

    monkeypatch.setattr(reranker_module, "_predict", fake_predict)
    outcome = rerank("问题", [Document(page_content="证据")])
    assert outcome.status == "cpu_fallback"
    assert calls == ["cuda", "cpu"]
    assert "out of memory" in (outcome.fallback_reason or "")


def test_cpu_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(reranker_module, "settings", _settings(reranker_device="cpu"))
    monkeypatch.setattr(reranker_module, "_predict", lambda *args: (_ for _ in ()).throw(RuntimeError("bad model")))
    outcome = rerank("问题", [Document(page_content="证据")])
    assert outcome.status == "failed"
    assert "bad model" in (outcome.fallback_reason or "")
