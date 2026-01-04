"""
Utility functions for the voice agent pipeline.
"""

import asyncio
from typing import Any, AsyncIterator, TypeVar


T = TypeVar("T")


async def merge_async_iters(*aiters: AsyncIterator[T]) -> AsyncIterator[T]:
    """
    Merge multiple async iterators into a single stream.

    Items are yielded as they become available from any iterator.
    The merged stream completes when all input iterators are exhausted.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    async def producer(aiter: AsyncIterator[Any]) -> None:
        async for item in aiter:
            await queue.put(item)
        await queue.put(sentinel)

    async with asyncio.TaskGroup() as tg:
        for aiter in aiters:
            tg.create_task(producer(aiter))

        finished = 0
        while finished < len(aiters):
            item = await queue.get()
            if item is sentinel:
                finished += 1
            else:
                yield item
