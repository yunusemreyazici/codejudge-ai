"""Trusted async batch-processing oracle for generated-test validation."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence


async def process_batch[InputT, ResultT](
    items: Sequence[InputT],
    worker: Callable[[InputT], Awaitable[ResultT]],
    concurrency: int,
) -> list[ResultT]:
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency <= 0:
        raise ValueError("concurrency must be a positive integer")
    semaphore = asyncio.Semaphore(concurrency)

    async def invoke(item: InputT) -> ResultT:
        async with semaphore:
            return await worker(item)

    tasks = [asyncio.create_task(invoke(item)) for item in items]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
