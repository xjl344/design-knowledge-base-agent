"""Unified async tool registry with validation, timeout and retry."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.resilience import is_permanent_model_error


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    duration_seconds: float = 0.0
    call_id: str = ""
    attempts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 3),
            "call_id": self.call_id,
            "attempts": self.attempts,
        }


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict[str, type] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_retries: int = 2


class ToolRegistry:
    def __init__(self, disabled_tools: set[str] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled_tools = set(disabled_tools or set())

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def call(self, name: str, **kwargs) -> ToolResult:
        call_id = str(uuid.uuid4())
        started = time.perf_counter()
        tool = self._tools.get(name)
        if name in self._disabled_tools:
            return ToolResult(
                False,
                error=f"web_disabled: {name} disabled by evaluation policy",
                duration_seconds=time.perf_counter() - started,
                call_id=call_id,
                attempts=0,
            )
        if tool is None:
            return ToolResult(False, error=f"未注册工具：{name}", duration_seconds=time.perf_counter() - started, call_id=call_id)
        for key, expected in tool.parameters.items():
            if key not in kwargs:
                return ToolResult(False, error=f"工具 {name} 缺少参数：{key}", duration_seconds=time.perf_counter() - started, call_id=call_id)
            if not isinstance(kwargs[key], expected):
                return ToolResult(False, error=f"工具 {name} 参数类型错误：{key}", duration_seconds=time.perf_counter() - started, call_id=call_id)
        attempts = 0
        last_error: Exception | None = None
        for attempts in range(1, tool.max_retries + 2):
            try:
                value = tool.func(**kwargs)
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=tool.timeout_seconds)
                return ToolResult(True, data=value, duration_seconds=time.perf_counter() - started, call_id=call_id, attempts=attempts)
            except asyncio.TimeoutError as exc:
                last_error = TimeoutError(
                    f"工具 {name} 执行超过 {tool.timeout_seconds:g} 秒"
                )
                if tool.max_retries == 0:
                    break
            except Exception as exc:
                last_error = exc
                if is_permanent_model_error(exc):
                    break
        error = str(last_error) if last_error else f"工具 {name} 执行失败"
        return ToolResult(False, error=error, duration_seconds=time.perf_counter() - started, call_id=call_id, attempts=attempts)
