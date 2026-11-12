"""In-memory fan-out of new readings to open /ws/live connections.

Runs inside one server process, so deploy with a single uvicorn worker.
"""
import asyncio
import time
from collections import defaultdict


class LiveHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._last_seen: dict[str, float] = {}

    def subscribe(self, device_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs[device_id].add(q)
        return q

    def unsubscribe(self, device_id: str, q: asyncio.Queue) -> None:
        self._subs[device_id].discard(q)
        if not self._subs[device_id]:
            self._subs.pop(device_id, None)

    def mark_seen(self, device_id: str, ts: float | None = None) -> None:
        ts = ts or time.time()
        if ts > self._last_seen.get(device_id, 0):
            self._last_seen[device_id] = ts

    def last_seen(self, device_id: str) -> float | None:
        return self._last_seen.get(device_id)

    def publish(self, device_id: str, payload: dict) -> None:
        for q in list(self._subs.get(device_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow browser; it will catch up from the next messages


hub = LiveHub()
