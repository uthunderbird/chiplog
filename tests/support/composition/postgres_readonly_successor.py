"""Shared builders extracted without changing their behavior."""

from chiplog.composition.postgres_readonly import HistoryReadUnavailable


def unavailable(*args: object) -> bytes:
    raise HistoryReadUnavailable("temporary read failure")
