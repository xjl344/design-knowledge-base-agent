"""Minimal replayable generator over a frozen evidence pack."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import re
import time
from typing import Any, Iterable

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from config import settings
from src.frozen_evidence import (
    FALLBACK_ANSWER_WITH_EVIDENCE,
    FALLBACK_ANSWER_WITHOUT_EVIDENCE,
    EvidencePack,
    soft_audit,
)
from src.generation_status import (
    PROVIDER_FAILURE_CLASSES,
    PROVIDER_HARD_ERROR_CLASSES,
    PROVIDER_LATENCY_CLASSES,
    RETRYABLE_FAILURE_CLASSES,
)
from src.model_clients import chat_model, model_descriptor, require_role_model


GENERATOR_V2_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """你是一个严格基于冻结证据包回答问题的设计知识库助手。

规则：
1. 只使用上下文中明确出现的信息，不使用模型常识补全资料。
2. 资料事实必须在句末紧跟允许的引用，例如 [L1]；不得生成上下文没有的引用编号。
3. 没有直接证据时，明确写“当前资料无法确认”，不要编造精确数字或无条件推荐。
4. 区分资料事实、有限推导和待验证内容；推导必须说明它不是资料原文。
5. 直接回答问题，优先使用短段落或项目符号，控制在约 600 字以内。
6. 不要输出七段式长报告，不要复述完整上下文，不要展示内部推理过程。
7. 只输出回答正文；不要附加控制标记、状态后缀或结束符。

{context}""",
    ),
    ("human", "问题：{question}"),
])
GENERATOR_PROMPT_VERSION = "generator-v2-20260919-r2"

# Replay measures a single model call per row, so retries stay off. This is the
# value the harness must pass; keeping it here means the recorded metadata and
# the actual call can never drift apart.
GENERATOR_MAX_RETRIES = 0

# A deadline the event loop enforces, on top of the HTTP client's own timeout.
#
# The client timeout is not sufficient in practice: one recorded attempt ran
# 9459 seconds against a 180-second `request_timeout`, because an HTTP-level
# timeout only fires when the socket goes quiet, and a relay that trickles bytes
# keeps resetting it.  The run's timing data is worthless if a row can hang for
# hours, so the call is additionally bounded by `asyncio.wait_for`, which the
# event loop enforces regardless of what the transport does.
#
# The margin keeps the client's own error (which carries the provider's
# message) as the usual outcome; this is the backstop, not the primary bound.
HARD_DEADLINE_MARGIN_SECONDS = 30.0


def _hard_deadline_seconds() -> float:
    return float(settings.llm_timeout_seconds) + HARD_DEADLINE_MARGIN_SECONDS


def _swallow_abandoned_result(task: "asyncio.Task[Any]") -> None:
    """Retrieve a cancelled task's outcome so it is not reported as unhandled.

    The abandoned call still finishes eventually, and its exception would
    otherwise surface as "Task exception was never retrieved" long after the
    row that abandoned it has been written.
    """
    if task.cancelled():
        return
    task.exception()


async def _call_with_deadline(awaitable: Any, timeout: float) -> Any:
    """Await ``awaitable``, abandoning it if it outlives ``timeout``.

    ``asyncio.wait_for`` is not enough here, and the difference cost eleven
    hours of wall clock.  On timeout it cancels the inner task and then *awaits
    the cancellation*; a transport that does not honour cancellation makes that
    await block for as long as the call would have taken anyway, so the guard
    provides no bound at all.  A recorded attempt ran 39286 seconds against a
    210-second deadline for exactly this reason.

    ``asyncio.wait`` returns as soon as the deadline passes without waiting for
    the cancellation to land, so the bound holds whatever the transport does.
    The abandoned task is cancelled and its result retrieved in a callback; it
    keeps its connection until it finishes, which is a cost worth paying to
    stop one row from stalling an entire run.
    """
    task = asyncio.ensure_future(awaitable)
    done, pending = await asyncio.wait({task}, timeout=timeout)
    if pending:
        task.cancel()
        task.add_done_callback(_swallow_abandoned_result)
        raise asyncio.TimeoutError(
            f"Request timed out: exceeded the hard deadline of {timeout:.0f}s."
        )
    return task.result()


# Control-token pollution observed in practice: the model occasionally appends
# a routing/control suffix such as ".calc" that is not part of the answer.
# These are stripped from the *normalised* answer only; the raw output is kept
# so the pollution stays visible in experiment results.
_TRAILING_CONTROL_RE = re.compile(
    r"(?:\s*[.。]?\s*(?:\.calc|\.analysis|\.final|\.answer|\.summary|\.end))+"
    r"\s*$",
    flags=re.I,
)
# Repeated end markers or stray tag leftovers.
_TRAILING_TAG_RE = re.compile(
    r"(?:\s*</?(?:answer|response|output|final)>\s*)+\s*$",
    flags=re.I,
)


def sanitize_answer(raw: str) -> tuple[str, list[str]]:
    """Return ``(clean_answer, applied_rules)`` without mutating the raw text.

    Sanitisation is intentionally narrow: it only removes trailing control
    artefacts.  Nothing inside the answer body is rewritten, so factual
    auditing always sees the same content the model produced.
    """
    text = str(raw or "")
    rules: list[str] = []

    stripped = _TRAILING_CONTROL_RE.sub("", text)
    if stripped != text:
        rules.append("remove_trailing_control_token")
        text = stripped

    stripped = _TRAILING_TAG_RE.sub("", text)
    if stripped != text:
        rules.append("remove_trailing_tag")
        text = stripped

    cleaned = text.rstrip()
    # Only report whitespace trimming when it is the *actual* difference, so a
    # token removal does not also masquerade as a whitespace fix.
    if cleaned != text:
        rules.append("rstrip_trailing_whitespace")
    return cleaned, rules


@dataclass(frozen=True)
class GenerationResult:
    question_id: str
    question: str
    answer: str
    model: str
    generation_latency_seconds: float
    error: str | None
    error_type: str | None
    attempt_count: int
    generation_status: str
    answer_status: str
    audit: dict[str, Any]
    raw_answer: str = ""
    sanitization_applied: bool = False
    sanitization_rules: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "answer": self.answer,
            # Raw model output is preserved so pollution remains auditable
            # instead of being silently rewritten away.
            "raw_answer": self.raw_answer,
            "sanitization_applied": self.sanitization_applied,
            "sanitization_rules": list(self.sanitization_rules),
            "model": self.model,
            "generation_latency_seconds": self.generation_latency_seconds,
            "error": self.error,
            "error_type": self.error_type,
            "attempt_count": self.attempt_count,
            "generation_status": self.generation_status,
            "answer_status": self.answer_status,
            "generation_success": self.error is None,
            "provider_error_type": self.error_type,
            "audit": self.audit,
        }


def _fallback_answer(pack: EvidencePack) -> str:
    if pack.items:
        return "当前生成模型不可用，无法生成正式回答；冻结证据已保留，请稍后重试。"
    return "当前资料无法确认，冻结证据包中没有可用的本地证据。"


def _classify_error(error: str | None) -> str | None:
    """Map a provider exception to a stable, machine-checkable failure class.

    The classes are deliberately split by *remedy*, not by vendor wording,
    because timeout and hard error need different fixes:

    - ``provider_timeout``   latency ceiling too tight -> raise timeout/ceiling
    - ``provider_rate_limit`` throughput ceiling hit  -> back off and retry
    - ``provider_error``     transport/5xx failure    -> retry or switch host
    - ``invalid_response``   call succeeded, payload unusable -> fix parsing/prompt
    - ``evaluation_error``   auditor itself raised    -> fix the evaluator

    ``invalid_response`` and ``evaluation_error`` are only produced outside
    this function (during sanitisation/auditing); they are listed here so the
    full vocabulary lives in one place.
    """
    if not error:
        return None
    lowered = error.lower()
    if any(token in lowered for token in ("timeout", "timed out", "deadline", "超时")):
        return "provider_timeout"
    if any(token in lowered for token in ("rate limit", "rate_limit", "429", "限流", "too many requests")):
        return "provider_rate_limit"
    return "provider_error"


# Failure-class vocabulary now lives in ``src/generation_status`` so that the
# aggregator (pure arithmetic, no model SDK) can read the same names without
# importing this module.  It is re-exported at the top of this file.

# Bounded exponential backoff between attempts, in seconds. Deliberately short:
# the goal is to ride out a stall, not to hammer the provider.
RETRY_BACKOFF_SECONDS = (1.0, 3.0)


def _build_chain():
    """Assemble the prompt | model | parser chain.

    Extracted so tests can substitute the whole expression instead of faking
    each link, and so the retry loop has one place to rebuild it per attempt.
    """
    return (
        GENERATOR_V2_PROMPT
        | chat_model("generator", streaming=False, temperature=0.0, max_retries=0)
        | StrOutputParser()
    )


def _retry_after_seconds(error_type: str | None, attempt_index: int) -> float:
    """Return the backoff before the next attempt.

    A rate limit gets the longest wait because retrying immediately is exactly
    what the provider asked us not to do.
    """
    base = RETRY_BACKOFF_SECONDS[min(attempt_index, len(RETRY_BACKOFF_SECONDS) - 1)]
    if error_type == "provider_rate_limit":
        return base * 3
    return base


async def generate_from_pack(
    pack: EvidencePack,
    *,
    expected_answer_spans: Iterable[dict[str, Any]] = (),
    expected_sources: Iterable[str] = (),
    required_terms: Iterable[Iterable[str]] = (),
    refusal_requirements: Iterable[Iterable[str]] = (),
    ambiguity_requirements: Iterable[Iterable[str]] = (),
    required_hops: Iterable[Any] = (),
    max_retries: int = GENERATOR_MAX_RETRIES,
) -> GenerationResult:
    """Generate from a pack, optionally retrying transient provider failures.

    Retrying never overwrites the original failure: every attempt is recorded
    in ``audit["attempts"]`` and the final ``status`` states how many failures
    preceded success.  A row that only succeeded on the third try is visibly
    different from one that succeeded immediately, which is what keeps a
    retried run from being mistaken for a stable one.
    """
    started = time.perf_counter()
    raw_answer = ""
    error: str | None = None
    attempts: list[dict[str, Any]] = []
    total_attempts = max(1, int(max_retries) + 1)

    for attempt_index in range(total_attempts):
        attempt_started = time.perf_counter()
        # Reset per attempt: without this a failed first attempt would leave
        # ``error`` set, so a retry that succeeded would still be recorded as
        # a failure and its answer discarded.
        error = None
        try:
            require_role_model("generator")
            chain = _build_chain()
            raw_answer = str(
                await _call_with_deadline(
                    chain.ainvoke({"question": pack.question, "context": pack.context_text()}),
                    _hard_deadline_seconds(),
                )
            ).strip()
        except (asyncio.TimeoutError, TimeoutError):
            # `asyncio.TimeoutError` is `TimeoutError` on 3.11+; naming both
            # keeps this correct across versions.  The message has to contain
            # "timed out" so `_classify_error` files it as provider_timeout --
            # the same class the client's own timeout produces, because the
            # remedy is identical.
            error = (
                f"Request timed out: exceeded the hard deadline of "
                f"{_hard_deadline_seconds():.0f}s."
            )
        except Exception as exc:
            error = str(exc)
        if error is not None:
            error_type = _classify_error(error)
            attempts.append(
                {
                    "attempt": attempt_index + 1,
                    "status": error_type or "provider_error",
                    "error": error,
                    "latency_seconds": round(time.perf_counter() - attempt_started, 3),
                }
            )
            if error_type not in RETRYABLE_FAILURE_CLASSES or attempt_index + 1 >= total_attempts:
                raw_answer = _fallback_answer(pack)
                break
            await asyncio.sleep(_retry_after_seconds(error_type, attempt_index))
            continue
        # Success.
        error = None
        attempts.append(
            {
                "attempt": attempt_index + 1,
                "status": "completed",
                "error": None,
                "latency_seconds": round(time.perf_counter() - attempt_started, 3),
            }
        )
        break

    answer, sanitization_rules = sanitize_answer(raw_answer)
    audit = soft_audit(
        answer,
        pack,
        expected_answer_spans,
        expected_sources,
        required_terms,
        refusal_requirements,
        ambiguity_requirements,
        required_hops,
    )
    if error is not None:
        # The answer is a canned fallback, not something the model produced.
        #
        # ``soft_audit`` already refuses to emit behaviour verdicts for a
        # detected substitute, which is what keeps the *replay* path honest.
        # This guard additionally clears the content metrics, and it has to
        # live on the generation side because only this layer knows the call
        # failed -- a replay of the recorded row cannot tell a timeout from a
        # genuine answer without re-running the provider.
        #
        # ``citation_metric_applicable``/``span_metric_applicable`` are forced
        # False: they describe the question, and leaving them True would pull a
        # row that produced no answer into the quality metrics.
        for key in (
            "citation_validity",
            "citation_id_usage_ratio",
            "source_coverage",
            "expected_answer_span_recall",
            "required_term_recall",
            "refusal_correctness",
            "ambiguity_safety",
            "hop_recall",
            "numeric_claim_citation_coverage",
            "unsupported_number_count",
            "unsupported_claim_count",
            "answer_length",
            "length_limit_exceeded",
        ):
            if key in audit:
                audit[key] = None
        audit["citation_metric_applicable"] = False
        audit["span_metric_applicable"] = False
        audit["hop_metric_applicable"] = False
        audit["numeric_citation_metric_applicable"] = False
        audit["warnings"] = list(audit.get("warnings") or []) + [
            {
                "status": "not_audited",
                "reason": (
                    f"生成失败（{_classify_error(error) or 'provider_error'}），"
                    "兜底文案不参与质量与行为判定"
                ),
            }
        ]
    audit["model_error"] = error
    audit["raw_answer_length"] = len(raw_answer)
    audit["sanitization_applied"] = bool(sanitization_rules)
    audit["sanitization_rules"] = list(sanitization_rules)
    error_type = _classify_error(error)
    generation_status = "completed" if error is None else error_type or "provider_error"
    audit["generation_success"] = error is None
    # Retry bookkeeping. ``attempt_count`` > 1 with a completed status means
    # the row only passed after a failure; ``attempts`` keeps the detail so a
    # retried success is never mistaken for a first-try success.
    audit["attempts"] = attempts
    audit["failed_attempt_count"] = sum(
        1 for item in attempts if item["status"] != "completed"
    )
    return GenerationResult(
        question_id=pack.question_id,
        question=pack.question,
        answer=answer,
        model=model_descriptor("generator")["model"],
        generation_latency_seconds=round(time.perf_counter() - started, 3),
        error=error,
        error_type=error_type,
        attempt_count=len(attempts),
        generation_status=generation_status,
        answer_status="generated" if error is None else "fallback",
        audit=audit,
        raw_answer=raw_answer,
        sanitization_applied=bool(sanitization_rules),
        sanitization_rules=tuple(sanitization_rules),
    )
