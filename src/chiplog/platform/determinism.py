from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DeterministicClock:
    """A finite clock used by deterministic platform and fault tests."""

    values: deque[datetime]

    def now(self) -> datetime:
        if not self.values:
            raise RuntimeError("deterministic clock exhausted")
        return self.values.popleft()


@dataclass
class DeterministicIdSource:
    """A finite ID source; exhaustion is explicit rather than random fallback."""

    values: deque[str]
    _seen: set[str] = field(default_factory=set, init=False)

    def next_id(self) -> str:
        if not self.values:
            raise RuntimeError("deterministic ID source exhausted")
        value = self.values.popleft()
        if value in self._seen:
            raise ValueError("deterministic ID source produced a duplicate")
        self._seen.add(value)
        return value
