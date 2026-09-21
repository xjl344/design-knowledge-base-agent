from pathlib import Path

from config import (
    CACHE_DIR,
    CHROMA_DIR,
    GRADIO_TEMP_DIR,
    ROOT_DIR,
    SHARED_PIP_CACHE_DIR,
    SHARED_WHEEL_DIR,
    TEMP_DIR,
    settings,
)


def test_project_generated_storage_stays_under_root():
    for path in (CACHE_DIR, CHROMA_DIR, GRADIO_TEMP_DIR, TEMP_DIR):
        assert path.is_relative_to(ROOT_DIR)


def test_embedding_model_uses_approved_shared_location():
    assert settings.embedding_model_path == settings.embedding_model_path.resolve()
    assert settings.embedding_model_path.drive.upper() == "E:"


def test_shared_python_artifacts_use_ai_infra():
    assert SHARED_PIP_CACHE_DIR == Path(r"E:\AI-Infra\pip-cache")
    assert SHARED_WHEEL_DIR == Path(r"E:\AI-Infra\wheels")
