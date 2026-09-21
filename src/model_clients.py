"""Role-based OpenAI-compatible chat model construction."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from config import settings


def model_descriptor(role: str) -> dict[str, str]:
    role = role.lower()
    if role == "planner":
        backend = settings.planner_backend
        model = settings.planner_model or ("Qwen2.5-7B-Instruct" if backend == "local" else settings.llm_model)
    elif role == "grader":
        backend = settings.grader_backend
        model = settings.grader_model or ("Qwen2.5-7B-Instruct" if backend == "local" else settings.llm_model)
    else:
        backend = "cloud"
        model = settings.llm_model
    if os.getenv("RAG_LOCAL_MODEL", "false").lower() == "true" and role == "generator":
        backend = "local"
        model = os.getenv("LOCAL_GENERATOR_MODEL", "qwen2.5:7b-instruct")
    if os.getenv("RAG_LOCAL_MODEL", "false").lower() == "true" and role in {"planner", "grader"}:
        backend = "local"
        model = os.getenv(
            "LOCAL_PLANNER_MODEL" if role == "planner" else "LOCAL_GRADER_MODEL",
            model,
        ) or "Qwen2.5-7B-Instruct"
    return {"role": role, "backend": backend, "model": model}


def require_role_model(role: str) -> None:
    descriptor = model_descriptor(role)
    if descriptor["backend"] != "local":
        settings.require_llm()


def chat_model(
    role: str,
    *,
    streaming: bool = False,
    temperature: float = 0.0,
    max_retries: int = 1,
) -> ChatOpenAI:
    role = role.lower()
    if role == "planner":
        backend, model, base_url, api_key = (
            settings.planner_backend,
            settings.planner_model,
            settings.planner_base_url,
            settings.planner_api_key,
        )
    elif role == "grader":
        backend, model, base_url, api_key = (
            settings.grader_backend,
            settings.grader_model,
            settings.grader_base_url,
            settings.grader_api_key,
        )
    else:
        backend, model, base_url, api_key = (
            "cloud",
            settings.llm_model,
            settings.llm_base_url,
            settings.llm_api_key,
        )

    if os.getenv("RAG_LOCAL_MODEL", "false").lower() == "true" and role == "generator":
        backend = "local"
        base_url = os.getenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
        api_key = os.getenv("LOCAL_MODEL_API_KEY", "local")
        model = os.getenv("LOCAL_GENERATOR_MODEL", "qwen2.5:7b-instruct")

    if backend == "local":
        model = model or "Qwen2.5-7B-Instruct"
        base_url = base_url or "http://127.0.0.1:11434/v1"
        api_key = api_key or "local"
    else:
        model = model or settings.llm_model
        base_url = base_url or settings.llm_base_url
        api_key = api_key or settings.llm_api_key

    # Evaluation can opt into local planner/grader backends without mutating
    # the user's .env or rebuilding the process-level Settings object.
    if os.getenv("RAG_LOCAL_MODEL", "false").lower() == "true" and role in {"planner", "grader"}:
        backend = "local"
        base_url = os.getenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
        api_key = os.getenv("LOCAL_MODEL_API_KEY", "local")
        model = os.getenv(
            "LOCAL_PLANNER_MODEL" if role == "planner" else "LOCAL_GRADER_MODEL",
            model,
        ) or "Qwen2.5-7B-Instruct"

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        streaming=streaming,
        timeout=settings.llm_timeout_seconds,
        max_retries=max(0, int(max_retries)),
    )
