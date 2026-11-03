"""In-memory sliding-window rate limiter (R18). Per process; holds no cluster data."""
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Optional


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, window: float = 60.0):
        self._clock, self._window = clock, window
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: int) -> Optional[int]:
        """Record a hit. Return None if allowed, else the seconds to wait (Retry-After)."""
        now = self._clock()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and now - q[0] >= self._window:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(self._window - (now - q[0])) + 1)
            q.append(now)
            return None
