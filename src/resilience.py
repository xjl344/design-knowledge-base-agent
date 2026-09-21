"""Runtime protections: circuit breaker, concurrency and token budgets."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


def is_permanent_model_error(exc: Exception) -> bool:
    """Account/authentication errors must not poison a transient breaker."""
    text = str(exc).lower()
    return any(marker in text for marker in ("402", "401", "403", "insufficient balance", "invalid api key", "authentication"))


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure_at: float | None = None

    def _before_call(self) -> None:
        if self.state != CircuitState.OPEN:
            return
        if self.last_failure_at and time.monotonic() - self.last_failure_at >= self.recovery_seconds:
            self.state = CircuitState.HALF_OPEN
            return
        raise CircuitOpenError("circuit breaker is open")

    async def call(self, func, *args, **kwargs):
        self._before_call()
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            if not is_permanent_model_error(exc):
                self.failures += 1
                self.last_failure_at = time.monotonic()
                if self.failures >= self.failure_threshold:
                    self.state = CircuitState.OPEN
            raise
        self.failures = 0
        self.state = CircuitState.CLOSED
        return result


class RequestLimiter:
    def __init__(self, max_concurrent: int = 5) -> None:
        self.semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def run(self, func, *args, **kwargs):
        async with self.semaphore:
            return await func(*args, **kwargs)


@dataclass(frozen=True)
class CostLimit:
    per_query_tokens: int = 10000
    daily_tokens: int = 1000000


class CostTracker:
    def __init__(self, limit: CostLimit) -> None:
        self.limit = limit
        self.daily_tokens = 0
        self.day_started = time.strftime("%Y-%m-%d")
        self.lock = Lock()

    def check_and_record(self, tokens: int) -> None:
        if tokens < 0 or tokens > self.limit.per_query_tokens:
            raise RuntimeError(f"单次查询 token 超过上限：{self.limit.per_query_tokens}")
        with self.lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day_started:
                self.day_started = today
                self.daily_tokens = 0
            if self.daily_tokens + tokens > self.limit.daily_tokens:
                raise RuntimeError(f"今日 token 配额已用尽：{self.limit.daily_tokens}")
            self.daily_tokens += tokens
