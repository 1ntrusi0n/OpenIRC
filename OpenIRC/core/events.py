"""Bounded event subscriptions cannot delay IRC message routing."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Event:
    kind: str
    data: Mapping = field(default_factory=lambda: MappingProxyType({}))
    timestamp: float = field(default_factory=time.time)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self, limit: int = 256) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(limit)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def emit(self, kind: str, **data) -> None:
        event = Event(kind, MappingProxyType(data))
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
