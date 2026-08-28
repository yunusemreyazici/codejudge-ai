import asyncio

import pytest
from solution import process_batch


def test_worker_failure_cancels_and_awaits_unfinished_tasks() -> None:
    async def scenario() -> None:
        blocker_started = asyncio.Event()
        blocker_cancelled = asyncio.Event()

        async def worker(value: int) -> int:
            if value == 1:
                await blocker_started.wait()
                raise RuntimeError("boom")
            blocker_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                blocker_cancelled.set()
            return value

        with pytest.raises(RuntimeError, match="boom"):
            await process_batch([1, 2], worker, 2)
        assert blocker_cancelled.is_set()

    asyncio.run(scenario())


def test_cancelling_batch_cleans_up_workers() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cancelled_count = 0

        async def worker(value: int) -> int:
            nonlocal cancelled_count
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled_count += 1
            return value

        processing = asyncio.create_task(process_batch([1, 2, 3], worker, 2))
        await started.wait()
        processing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await processing
        assert cancelled_count == 2

    asyncio.run(scenario())


def test_concurrency_larger_than_batch_is_supported() -> None:
    async def worker(value: int) -> int:
        return value + 1

    assert asyncio.run(process_batch([1, 2], worker, 10)) == [2, 3]
