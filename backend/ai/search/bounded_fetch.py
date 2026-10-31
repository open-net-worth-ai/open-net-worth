"""A page read that cannot outlive its deadline, on threads that are not the app's.

Behind the ``search_reads_results`` flag; ``page_reader`` is the only caller. ``plain_get_fetch``
(main's ``fetch_page`` path) is untouched: it has a 60 s socket timeout and no wall-clock limit,
so a slow-drip server keeps its worker thread for as long as it likes, and a read abandoned by
``asyncio.wait_for`` keeps the thread too. That is acceptable for one deliberate ``fetch_page``
call; it is not for three reads per search, so the reads get this variant instead:

* ``fetch_text_until`` downloads in chunks and stops at an absolute deadline (a per-socket
  timeout alone is defeated by a server that sends a byte at a time), and kills the extraction
  child rather than let it run past it. Same guards as main: ``pinned_urlopen`` validates and
  pins every hop (``assert_public_url``), TLS goes through ``https_context()``, the body is capped
  at ``_MAX_FETCH_BYTES``.
* ``run_bounded`` runs work on a small dedicated pool behind a semaphore, so abandoned reads
  can never starve ``asyncio.to_thread`` for the rest of the app. The semaphore is released when
  the THREAD finishes, not when the awaiting task is cancelled, so it really bounds the threads.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from backend.ai.search.base import (
    _MAX_FETCH_BYTES,
    _MAX_FETCH_REDIRECTS,
    _MIN_EXTRACTED_CHARS,
)

T = TypeVar("T")

# Concurrent page reads (and excerpt builds) across the whole process.
MAX_READERS = 4
# One socket operation. The deadline is checked between chunks, so this bounds the overshoot.
_SOCKET_TIMEOUT_S = 2.0
_CHUNK_BYTES = 65_536
# Not worth spawning the extraction child with less than this left.
_MIN_EXTRACT_S = 0.5

_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_READERS, thread_name_prefix="search-read")
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


class ReadDeadlineExceeded(TimeoutError):
    """The read ran out of its wall-clock budget."""


def _semaphore() -> asyncio.Semaphore:
    # An asyncio.Semaphore belongs to one loop; the app has one, tests make many.
    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = _slots[loop] = asyncio.Semaphore(MAX_READERS)
    return sem


async def run_bounded(fn: Callable[..., T], *args: Any) -> T:
    """``fn(*args)`` on the dedicated pool, at most ``MAX_READERS`` at once. Cancelling the
    caller abandons the result, never the slot: the slot frees when the thread returns."""
    loop = asyncio.get_running_loop()
    sem = _semaphore()
    await sem.acquire()

    def _free(_future: Any) -> None:
        try:
            loop.call_soon_threadsafe(sem.release)
        except RuntimeError:  # the loop closed while an abandoned read was finishing
            pass

    try:
        future = _EXECUTOR.submit(fn, *args)
    except BaseException:
        sem.release()
        raise
    future.add_done_callback(_free)
    return await asyncio.wrap_future(future)


def fetch_text_until(url: str, deadline: float) -> str:
    """The readable text of ``url``, or an in-band ``[fetch …]`` failure string like
    ``plain_get_fetch``; raises ``ReadDeadlineExceeded`` once ``time.monotonic()`` passes
    ``deadline``. Blocking — call through ``run_bounded``."""
    from backend.ai.search.html_extract import extract_main_content
    from backend.core.net_guard import UnsafeUrlError
    from backend.core.pinned_http import pinned_urlopen

    def _remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise ReadDeadlineExceeded(url)
        return left

    try:
        with pinned_urlopen(
            url,
            headers={"User-Agent": "OpenNetWorth/1.0"},
            timeout=min(_SOCKET_TIMEOUT_S, _remaining()),
            max_redirects=_MAX_FETCH_REDIRECTS,
        ) as resp:
            read = getattr(resp, "read1", None) or resp.read
            chunks: list[bytes] = []
            size = 0
            while size < _MAX_FETCH_BYTES:
                _remaining()
                chunk = read(_CHUNK_BYTES)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            charset = resp.headers.get_content_charset() or "utf-8"
    except UnsafeUrlError as exc:
        return f"[fetch blocked: {exc}]"
    except TimeoutError as exc:
        raise ReadDeadlineExceeded(url) from exc
    except Exception as exc:
        return f"[fetch failed: {exc}]"

    raw = b"".join(chunks)[:_MAX_FETCH_BYTES].decode(charset, errors="replace")
    if not raw.strip():
        return "[no content extracted]"
    if _remaining() < _MIN_EXTRACT_S:
        raise ReadDeadlineExceeded(url)
    extracted = extract_main_content(raw, url, timeout_s=_remaining())
    _remaining()
    if extracted and len(extracted.strip()) >= _MIN_EXTRACTED_CHARS:
        return extracted.strip()
    return raw
