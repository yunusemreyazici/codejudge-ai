import asyncio

import pytest
from solution import process_batch


def test_empty_input_and_invalid_concurrency() -> None:
    async def worker(value: int) -> int:
        return value

    assert asyncio.run(process_batch([], worker, 1)) == []
    for concurrency in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            asyncio.run(process_batch([], worker, concurrency))


def test_worker_is_called_once_per_item_and_results_preserve_input_order() -> None:
    async def scenario() -> None:
        release_first = asyncio.Event()
        second_completed = asyncio.Event()
        calls: list[str] = []

        async def worker(value: str) -> str:
            calls.append(value)
            if value == "first":
                await release_first.wait()
            else:
                second_completed.set()
            return value.upper()

        processing = asyncio.create_task(process_batch(["first", "second"], worker, 2))
        await second_completed.wait()
        release_first.set()
        assert await processing == ["FIRST", "SECOND"]
        assert sorted(calls) == ["first", "second"]

    asyncio.run(scenario())


def test_concurrency_limit_is_respected_and_parallelism_is_used() -> None:
    async def scenario() -> None:
        active = 0
        maximum = 0
        two_started = asyncio.Event()
        release = asyncio.Event()

        async def worker(value: int) -> int:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1
            return value * 2

        processing = asyncio.create_task(process_batch([1, 2, 3, 4], worker, 2))
        await two_started.wait()
        assert maximum == 2
        release.set()
        assert await processing == [2, 4, 6, 8]
        assert maximum == 2

    asyncio.run(scenario())
