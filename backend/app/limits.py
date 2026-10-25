"""Sliding-window rate limits, shared by the API, the MCP server and the Slack bot (D081, D124).

A limit is per key: the signed-in person where there is one, else the client's address, so a
team behind one office address or one TLS proxy doesn't share a single budget. In memory: one
process. Several API replicas need a shared store (the review's Phase 3).
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """A sliding window (one minute by default) per key. A swarm of many keys grows the table,
    so keys whose window has emptied are swept now and then."""

    SWEEP_EVERY = 5_000  # calls between sweeps of keys whose window has emptied

    def __init__(self, per_minute: int, window: float = 60):
        self.per_minute = per_minute  # the limit per window (per day for a 24 h window)
        self.window = window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()
        self.calls = 0

    def allow(self, key: str = "*", now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            self.calls += 1
            if self.calls % self.SWEEP_EVERY == 0:
                stale = [k for k, h in self.hits.items() if not h or now - h[-1] > self.window]
                for k in stale:
                    del self.hits[k]
            hits = self.hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def person_limits() -> tuple[RateLimiter, RateLimiter]:
    """A fresh pair of per-person question limits (a minute, a day) from the settings."""
    from app.config import get_settings

    s = get_settings()
    return (
        RateLimiter(s.yaan_ask_per_person_per_minute),
        RateLimiter(s.yaan_ask_per_person_per_day, window=86_400),
    )
