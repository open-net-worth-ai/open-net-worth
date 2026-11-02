"""Hop between the voice worker's own event loop and the app's.

The LiveKit worker runs on a loop of its own in a background thread (its jobs in threads of
their own), while Lucius's turns, the live stream and the stores belong to the app's loop.
``stream`` runs an async generator on the APP loop and hands its items to the caller's loop
as they arrive; ``call`` does the same for one coroutine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
_DONE = object()


async def call(app_loop: asyncio.AbstractEventLoop, coro: Awaitable[T]) -> T:
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, app_loop))


async def stream(
    app_loop: asyncio.AbstractEventLoop, make: Callable[[], AsyncIterator[T]]
) -> AsyncIterator[T]:
    """Yield what ``make()`` yields, running it on ``app_loop``. Closing this generator (the
    caller hung up) cancels the producer on the app loop too."""
    here = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    async def produce() -> None:
        try:
            async for item in make():
                here.call_soon_threadsafe(queue.put_nowait, item)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's side
            here.call_soon_threadsafe(queue.put_nowait, exc)
            return
        here.call_soon_threadsafe(queue.put_nowait, _DONE)

    future = asyncio.run_coroutine_threadsafe(produce(), app_loop)
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                if isinstance(item, asyncio.CancelledError):
                    return
                raise item
            yield item
    finally:
        future.cancel()
