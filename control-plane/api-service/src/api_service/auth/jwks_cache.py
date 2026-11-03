"""Cache of token signing keys (D19, N8-N13).

- keys are cached per process and refetched after a TTL
- an unknown kid triggers one refetch, rate limited by a cooldown so random kids cannot flood the provider
- during a provider outage cached keys stay valid up to a maximum stale age, then validation fails closed
- one fetch at a time: concurrent callers share the lock
"""
import logging
import threading
import time
from typing import Callable, Optional

import jwt

logger = logging.getLogger("api_service.auth")


class KeyUnavailable(Exception):
    """No usable signing key: unknown kid, or the provider is down and the cache is too old."""


class JwksCache:
    def __init__(self, fetcher: Callable[[], dict], ttl: int = 600, cooldown: int = 30,
                 max_stale: int = 3600, clock: Callable[[], float] = time.time):
        self._fetch = fetcher
        self._ttl, self._cooldown, self._max_stale, self._clock = ttl, cooldown, max_stale, clock
        self._lock = threading.Lock()
        self._keys: dict = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt: Optional[float] = None
        self.fetch_count = 0

    def _can_attempt(self, now: float) -> bool:
        return self._last_attempt is None or now - self._last_attempt >= self._cooldown

    def _refresh(self, now: float) -> bool:
        self._last_attempt = now
        try:
            jwks = self._fetch()
            keys = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(k) for k in jwks.get("keys", []) if "kid" in k}
        except Exception as e:  # noqa: BLE001 - any provider failure is handled the same way
            logger.warning("signing key fetch failed: %s", type(e).__name__)
            return False
        finally:
            self.fetch_count += 1
        self._keys, self._fetched_at = keys, now
        return True

    def get_key(self, kid: Optional[str]):
        if not kid:
            raise KeyUnavailable("token has no kid")
        with self._lock:
            now = self._clock()
            expired = self._fetched_at is None or now - self._fetched_at > self._ttl
            if expired and self._can_attempt(now):
                self._refresh(now)
            if self._fetched_at is None or now - self._fetched_at > self._ttl + self._max_stale:
                raise KeyUnavailable("signing keys unavailable")
            if kid not in self._keys and self._can_attempt(now):
                self._refresh(now)
            key = self._keys.get(kid)
            if key is None:
                raise KeyUnavailable("no signing key for kid")
            return key
