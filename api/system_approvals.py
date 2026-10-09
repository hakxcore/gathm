"""In-memory, expiring, owner-bound approvals for exact typed system actions."""
from __future__ import annotations

import copy
import secrets
import threading
import time


class ApprovalStore:
    def __init__(self, ttl=120, capacity=64):
        self.ttl, self.capacity = ttl, capacity
        self._lock = threading.Lock()
        self._items = {}

    def _expire(self):
        now = time.monotonic()
        for key in list(self._items):
            if self._items[key][0] <= now:
                del self._items[key]

    def create(self, owner, plan):
        if not owner or not plan.get("requires_confirmation"):
            raise ValueError("Only a named caller's confirmed actions can be queued.")
        with self._lock:
            self._expire()
            if len(self._items) >= self.capacity or sum(item[1] == owner for item in self._items.values()) >= 8:
                raise ValueError("Too many pending system actions. Cancel one or wait for it to expire.")
            key = secrets.token_urlsafe(32)
            self._items[key] = (time.monotonic() + self.ttl, owner, copy.deepcopy(plan))
            return key, time.time() + self.ttl

    def take(self, key, owner):
        """Consume before execution; failed execution never makes a ticket reusable."""
        with self._lock:
            self._expire()
            item = self._items.get(key)
            if item is None or not secrets.compare_digest(item[1], owner):
                raise LookupError("This approval expired, was already used, or belongs to another session.")
            del self._items[key]
            return item[2]

    def cancel_owner(self, owner):
        with self._lock:
            for key in list(self._items):
                if self._items[key][1] == owner:
                    del self._items[key]

    def clear(self):
        with self._lock:
            self._items.clear()
