"""Bounding calls that do not cooperate with cancellation.

This project has measured the failure twice: 9458 seconds behind a correctly
configured HTTP timeout, and 39286 seconds behind `asyncio.wait_for`.  Then it
happened a third time, in a retrieval run: **one question took 42019 seconds
while the other seventeen together took about two hours**, because the query
decomposition call was not bounded at all.

The tests below pin the property that matters -- an awaitable that *ignores*
cancellation must still be cut off -- and the integration point that was missing.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.async_utils import swallow_abandoned_result, wait_bounded


def test_a_quick_awaitable_returns_its_value():
    async def main():
        async def quick():
            return "done"

        return await wait_bounded(quick(), 5.0, "超时")

    assert asyncio.run(main()) == "done"


def test_a_slow_awaitable_is_cut_off():
    async def main():
        async def slow():
            await asyncio.sleep(30)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            await wait_bounded(slow(), 0.2, "超时")

    started = time.perf_counter()
    asyncio.run(main())
    assert time.perf_counter() - started < 5, "超时后不该继续等"


def test_an_awaitable_that_swallows_cancellation_is_still_cut_off():
    """The whole reason `wait_for` was abandoned.

    `wait_for` cancels the inner task and then *awaits the cancellation*.  A
    coroutine that catches `CancelledError` and keeps going therefore blocks
    that await until it finishes on its own, and the timeout bounds nothing.
    `asyncio.wait` returns when the timeout fires regardless.
    """
    async def main():
        async def stubborn():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # Deliberately uncooperative: this is what a transport that
                # ignores cancellation does.
                await asyncio.sleep(30)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            await wait_bounded(stubborn(), 0.3, "超时")

    started = time.perf_counter()
    asyncio.run(main())
    assert time.perf_counter() - started < 5, (
        "吞掉 CancelledError 的协程也必须被截断；旧实现下这里会挂满 30 秒"
    )


def test_an_expired_deadline_does_not_start_the_call():
    """A deadline already in the past must not launch work at all."""
    started = False

    async def main():
        nonlocal started
        async def work():
            nonlocal started
            started = True
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await wait_bounded(work(), -1.0, "超时")

    asyncio.run(main())
    assert started is False


def test_an_exception_inside_the_awaitable_propagates():
    """A real failure must not be reported as a timeout."""
    async def main():
        async def boom():
            raise ValueError("真实错误")

        with pytest.raises(ValueError, match="真实错误"):
            await wait_bounded(boom(), 5.0, "超时")

    asyncio.run(main())


def test_swallowing_an_abandoned_result_drains_its_exception():
    """Otherwise asyncio logs "exception was never retrieved" much later."""
    async def main():
        async def boom():
            raise ValueError("abandoned")

        task = asyncio.ensure_future(boom())
        task.add_done_callback(swallow_abandoned_result)
        await asyncio.sleep(0.05)
        return task

    task = asyncio.run(main())
    assert task.done()


# ---------------------------------------------------------------------------
# The integration point that was actually missing.
# ---------------------------------------------------------------------------


def test_query_decomposition_is_bounded_by_the_retrieval_deadline(monkeypatch):
    """The defect this file was written for.

    `retrieve_documents_multi` bounded `retrieve_documents` and the sub-query
    retrievals but **not** `decompose_question`.  With the provider hanging, one
    question in a real run took 42019 seconds.  A guard that checks the deadline
    before the call is not the same as a guard that bounds it.
    """
    import src.retriever as retriever

    async def hanging_decompose(question):
        await asyncio.sleep(300)
        return ["never"]

    monkeypatch.setattr(
        "src.query_decompose.decompose_question", hanging_decompose
    )
    monkeypatch.setattr(retriever, "retrieve_documents", lambda *a, **k: [])

    started = time.perf_counter()
    result = asyncio.run(
        retriever.retrieve_documents_multi("q", total_timeout_seconds=1.0)
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 15, f"分解调用未被约束，耗时 {elapsed:.1f}s"
    assert result == []
