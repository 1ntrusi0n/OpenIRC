"""Monotonic token buckets and bounded failure windows."""
from __future__ import annotations

import time
from collections import deque


class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(0.01, rate)
        self.capacity = max(1.0, capacity if capacity is not None else rate * 2)
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def allow(self, amount: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < amount:
            return False
        self.tokens -= amount
        return True


class FailureWindow:
    def __init__(self, limit: int, seconds: float):
        self.limit, self.seconds = limit, seconds
        self.failures: dict[str, deque[float]] = {}

    def blocked(self, key: str) -> bool:
        values = self.failures.get(key, deque())
        now = time.monotonic()
        while values and values[0] <= now - self.seconds:
            values.popleft()
        if not values:
            self.failures.pop(key, None)
        return len(values) >= self.limit

    def fail(self, key: str) -> None:
        self.blocked(key)
        if len(self.failures) >= 10000 and key not in self.failures:
            self.failures.pop(next(iter(self.failures)))
        self.failures.setdefault(key, deque(maxlen=self.limit)).append(time.monotonic())

    def clear(self, key: str) -> None:
        self.failures.pop(key, None)
