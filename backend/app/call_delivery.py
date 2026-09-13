"""Bounded, de-duplicated delivery tasks for short-lived call events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic, time
from typing import Awaitable, Callable, Hashable


DeliveryKey = tuple[Hashable, ...]


@dataclass(frozen=True)
class DeliveryResult:
    key: DeliveryKey
    status: str
    elapsed_ms: int
    delivered_count: int = 0
    error_type: str | None = None


class CallDeliveryCoordinator:
    """Own all call delivery tasks so none are silently orphaned."""

    def __init__(self, *, now_ms: Callable[[], int] | None = None) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._keys: set[DeliveryKey] = set()
        self._now_ms = now_ms or (lambda: int(time() * 1000))

    @property
    def pending_count(self) -> int:
        return len(self._tasks)

    def schedule(
        self,
        key: DeliveryKey,
        operation: Callable[[], Awaitable[int | None]],
        *,
        timeout_seconds: float,
        expires_at_ms: int | None = None,
        on_result: Callable[[DeliveryResult], None] | None = None,
    ) -> bool:
        if key in self._keys:
            return False
        if expires_at_ms is not None and expires_at_ms <= self._now_ms():
            return False
        self._keys.add(key)

        async def run() -> None:
            started = monotonic()
            try:
                delivered = await asyncio.wait_for(operation(), timeout_seconds)
                result = DeliveryResult(
                    key=key,
                    status="success",
                    elapsed_ms=round((monotonic() - started) * 1000),
                    delivered_count=int(delivered or 0),
                )
            except asyncio.TimeoutError:
                result = DeliveryResult(
                    key=key,
                    status="timeout",
                    elapsed_ms=round((monotonic() - started) * 1000),
                    error_type="TimeoutError",
                )
            except Exception as exc:
                result = DeliveryResult(
                    key=key,
                    status="failed",
                    elapsed_ms=round((monotonic() - started) * 1000),
                    error_type=type(exc).__name__,
                )
            finally:
                self._keys.discard(key)
            if on_result is not None:
                try:
                    on_result(result)
                except Exception:
                    # Diagnostics and status reporting are best-effort. A
                    # reporting failure must not turn a completed delivery
                    # path into an unhandled background task.
                    pass

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
