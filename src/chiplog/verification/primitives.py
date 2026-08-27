from __future__ import annotations

from dataclasses import dataclass


class UnreachedFaultError(AssertionError):
    pass


@dataclass
class FaultPoint:
    name: str
    reached: int = 0

    def hit(self) -> None:
        self.reached += 1

    def require_reached(self) -> None:
        if self.reached == 0:
            raise UnreachedFaultError(f"fault point was not reached: {self.name}")


@dataclass(frozen=True)
class StateObservation[T]:
    before: T
    after: T

    @property
    def changed(self) -> bool:
        return self.before != self.after
