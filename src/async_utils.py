"""Helpers for bounding calls that may not cooperate with cancellation.

The lesson this module exists to hold
-------------------------------------
`asyncio.wait_for` cancels the inner task and then **awaits the cancellation**.
When the transport ignores cancellation, that await blocks until the call ends
on its own, so the timeout bounds nothing.  This project has measured that twice:
9458 seconds behind an HTTP timeout that was correctly configured, and 39286
seconds behind `wait_for`.

`asyncio.wait({task}, timeout=...)` returns the moment the timeout fires and
does not wait for the cancellation to land.  That is the only shape that holds
when the other side does not cooperate.

The abandoned task still finishes eventually, and if its exception is never
retrieved asyncio reports "Task exception was never retrieved" long after the
row that abandoned it has been written -- so the callback below drains it.
"""

from __future__ import annotations

import asyncio
from typing import Any


def swallow_abandoned_result(task: "asyncio.Task[Any]") -> None:
    """Retrieve a cancelled task's outcome so it is not reported as unhandled.

    Attach with `task.add_done_callback(swallow_abandoned_result)` right after
    cancelling a task that was abandoned on a deadline.
    """
    if task.cancelled():
        return
    task.exception()


async def wait_bounded(awaitable: Any, remaining: float, message: str) -> Any:
    """Await `awaitable`, giving up after `remaining` seconds.

    Raises `asyncio.TimeoutError` with `message` when the deadline fires.  The
    abandoned task is cancelled and drained; it is **not** awaited, which is the
    whole point.
    """
    if remaining <= 0:
        raise asyncio.TimeoutError(message)
    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=remaining)
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(swallow_abandoned_result)
    raise asyncio.TimeoutError(message)
