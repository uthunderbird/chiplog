"""Policy-free process mechanisms.

The physical SQLite writer is deliberately not re-exported. Capability code must
receive a capability-owned persistence port from composition instead of importing
a generic authoritative write surface from :mod:`chiplog.platform`.
"""

from .determinism import DeterministicClock, DeterministicIdSource

__all__ = ["DeterministicClock", "DeterministicIdSource"]
